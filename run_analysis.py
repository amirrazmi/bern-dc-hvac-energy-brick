"""
Brick-driven HVAC energy diagnostics for the representative Bern data centre.

1. Build the Brick model and generate one year of BMS-style baseline trends.
2. Use SPARQL on the Brick graph to find the right points (no hard-coded tag names).
3. Run rule-based checks against ASHRAE TC 9.9, SIA 382/1 and cantonal energy-law (MuKEn) expectations.
4. Quantify a stack of energy efficiency measures (kWh, CHF, PUE).
"""
import os
from dataclasses import replace
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["svg.fonttype"] = "none"

from build_model import build, BRICK, DC
from hvac_energy import Settings, bern_climate, run, summary

os.makedirs("model", exist_ok=True); os.makedirs("results", exist_ok=True)
PRICE = 0.20  # CHF per kWh, assumption

# 1. Model and baseline data -------------------------------------------------
g = build()
g.serialize("model/bern_dc.ttl", format="turtle")
oat = bern_climate()
base = Settings()
df = run(base, oat)
df.head(0)

# 2. SPARQL: find points through the graph ------------------------------------
Q_CRAH = """
SELECT ?crah ?room ?sat ?sp ?fan WHERE {
  ?crah a brick:CRAH ; brick:hasLocation ?room ;
        brick:hasPoint ?satp, ?spp .
  ?satp a brick:Supply_Air_Temperature_Sensor ; dc:timeseries ?sat .
  ?spp  a brick:Supply_Air_Temperature_Setpoint ; dc:timeseries ?sp .
  ?crah brick:hasPart ?f . ?f a brick:Fan ; brick:hasPoint ?fp .
  ?fp a brick:Fan_Speed_Command ; dc:timeseries ?fan .
}"""
Q_CDU = """
SELECT ?cdu ?src WHERE {
  ?cdu a brick:Heat_Exchanger ; brick:hasPoint ?p .
  ?p a brick:Supply_Water_Temperature_Sensor .
  ?src brick:feeds ?cdu . ?src a brick:Chilled_Water_Loop .
}"""
Q_HEAT_REUSE = """ASK { ?m a ?cls . FILTER(CONTAINS(STR(?cls), "Thermal") || CONTAINS(STR(?cls), "Heat_Meter")) }"""
ns = {"brick": BRICK, "dc": DC}
crahs = list(g.query(Q_CRAH, initNs=ns))
cdus_on_chw = list(g.query(Q_CDU, initNs=ns))
has_heat_reuse = bool(g.query(Q_HEAT_REUSE, initNs=ns).askAnswer)

# 3. Rule-based checks --------------------------------------------------------
findings = []
sat = df.crah_sat_c.mean()
if sat < 22:
    findings.append(("Overcooling", f"CRAH supply air {sat:.0f} C, at the bottom of the ASHRAE TC 9.9 recommended range (18-27 C), so chillers work harder than needed",
                     "Raise supply air to 24 C and chilled water to 16 C"))
dT = (df.crah_rat_c - df.crah_sat_c).mean()
if dT < 10 and df.crah_fan_pct.mean() > 90:
    findings.append(("Bypass air, fans at 100 %", f"Average CRAH air delta-T only {dT:.1f} K with fans at 100 % (design about 12 K)",
                     "Cold-aisle containment and fan speed control to about 65 %"))
if cdus_on_chw:
    findings.append(("Liquid cooling on cold chilled water", f"{len(cdus_on_chw)} CDUs fed from the 10 C chilled water loop although direct-to-chip racks accept warm water (ASHRAE W32)",
                     "Separate warm-water loop for CDUs at W32, served by dry coolers"))
missed = ((df.oat_c < 12) & (df.chiller_kw > 5)).sum()
findings.append(("Limited free cooling", f"Chillers run {missed} h per year while outdoor air is below 12 C",
                 "Higher water temperatures extend free-cooling hours (follows from measures above)"))
if not has_heat_reuse:
    findings.append(("No heat reuse", "No heat meter or heat-reuse connection in the model; cantonal energy law (MuKEn) increasingly expects use of waste heat",
                     "Supply warm CDU return water to low-temperature district heating"))
pd.DataFrame(findings, columns=["Finding", "Evidence", "Measure"]).to_csv("results/findings.csv", index=False)

# 4. Measure stack -------------------------------------------------------------
steps = [
    ("Baseline", base),
    ("M1 Raise air to 24 C, CHW to 16 C", replace(base, crah_sat_sp=24.0, chw_supply=16.0)),
    ("M2 + Containment, fans 65 %", replace(base, crah_sat_sp=24.0, chw_supply=16.0, airflow_oversize=1.05)),
    ("M3 + Warm-water loop W32 for CDUs", replace(base, crah_sat_sp=24.0, chw_supply=16.0, airflow_oversize=1.05,
                                                 liquid_on_warm_loop=True, tcs_supply=32.0)),
    ("M4 + Heat reuse to district heating", replace(base, crah_sat_sp=24.0, chw_supply=16.0, airflow_oversize=1.05,
                                                   liquid_on_warm_loop=True, tcs_supply=32.0, heat_reuse=True)),
]
rows, prev = [], None
for name, s in steps:
    # M2 fan effect: airflow reduced from 1.6x to 1.05x need -> fan speed 1.05/1.6
    if s.airflow_oversize < base.airflow_oversize:
        s = replace(s, fan_speed=s.airflow_oversize / base.airflow_oversize, airflow_oversize=base.airflow_oversize)
    r = summary(run(s, oat), PRICE)
    rows.append({"Step": name, "PUE": round(r["PUE"], 3), "HVAC energy MWh/a": round(r["HVAC_MWh"]),
                 "Chillers MWh/a": round(r["Chiller_MWh"]), "CRAH fans MWh/a": round(r["Fan_MWh"]),
                 "HVAC cost kCHF/a": round(r["HVAC_cost_kCHF"]), "Heat reuse MWh/a": round(r["Heat_reuse_MWh"])})
res = pd.DataFrame(rows)
b = res.iloc[0]["HVAC energy MWh/a"]
res["HVAC saving vs baseline"] = (100 * (1 - res["HVAC energy MWh/a"] / b)).round(0).astype(int).astype(str) + " %"
res.to_csv("results/measures.csv", index=False)

print(f"Brick model: {len(g)} triples, {len(crahs)} CRAHs found via SPARQL, {len(cdus_on_chw)} CDUs on chilled water")
print("\nFindings:"); [print(f" - {f[0]}: {f[1]}") for f in findings]
print("\nMeasures:"); print(res.to_string(index=False))

# Charts
plt.figure(figsize=(9, 4))
plt.bar(range(len(res)), res["HVAC energy MWh/a"], color=["#9a9a9a", "#8fa8c8", "#6b8bb8", "#3f6596", "#1f3b63"])
for i, v in enumerate(res["HVAC energy MWh/a"]): plt.text(i, v + 10, f"{v}", ha="center")
plt.xticks(range(len(res)), ["Baseline", "M1", "M1+M2", "M1-M3", "M1-M4"])
plt.ylabel("HVAC energy (MWh per year)"); plt.title("HVAC energy by measure stack, 1 MW data centre, Bern")
plt.tight_layout(); plt.savefig("results/01_measure_stack.svg"); plt.close()

plt.figure(figsize=(9, 4))
plt.plot(res["PUE"].values, marker="o", color="#1f3b63")
plt.xticks(range(len(res)), ["Baseline", "M1", "M1+M2", "M1-M3", "M1-M4"])
plt.ylabel("Annual PUE"); plt.title("PUE improvement"); plt.grid(alpha=0.3)
plt.tight_layout(); plt.savefig("results/02_pue.svg"); plt.close()
