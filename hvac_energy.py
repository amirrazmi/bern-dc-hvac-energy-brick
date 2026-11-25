"""
Hourly HVAC energy model for the representative Bern data centre.
Used twice: to generate BMS-style baseline trend data, and to evaluate
energy efficiency measures. First-order physics for concept-stage decisions.
"""
from dataclasses import dataclass, replace
import numpy as np
import pandas as pd

RHO, CP = 1.2, 1005.0
# Approximate Bern monthly mean air temperatures (C), climate-normal order of magnitude
BERN_MONTHLY_MEAN = [-0.5, 0.8, 4.8, 8.6, 13.0, 16.4, 18.5, 17.9, 13.9, 9.4, 4.0, 0.7]
HOURS = [744, 672, 744, 720, 744, 720, 744, 744, 720, 744, 720, 744]


def bern_climate(seed=7, amp=4.5, sd=2.0):
    """Synthetic hourly outdoor temperature for one year (replace with MeteoSwiss Bern-Zollikofen data)."""
    rng = np.random.default_rng(seed)
    out = []
    for m, h in zip(BERN_MONTHLY_MEAN, HOURS):
        k = np.arange(h)
        daily = amp * np.sin(2 * np.pi * ((k % 24) - 11) / 24)
        noise = np.convolve(rng.normal(0, sd, h + 48), np.ones(48) / np.sqrt(48), "same")[:h]
        out.append(m + daily + noise)
    return np.concatenate(out)


@dataclass
class Settings:
    it_air_kw: float = 400.0          # Hall A, air-cooled
    it_liquid_kw: float = 600.0       # Hall B, direct-to-chip
    d2c_capture: float = 0.8          # share of Hall B heat into the liquid loop
    crah_sat_sp: float = 18.0         # CRAH supply air setpoint (C)
    chw_supply: float = 10.0          # chilled water supply (C)
    fan_speed: float = 1.0            # CRAH fan speed (1 = 100 %)
    airflow_oversize: float = 1.6     # airflow vs design need (bypass air, no containment)
    crah_fan_kw_full: float = 11.0    # per CRAH at 100 % speed
    n_crah: int = 4
    liquid_on_warm_loop: bool = False # CDUs on a separate warm-water loop?
    tcs_supply: float = 25.0          # CDU secondary (IT side) supply temperature (C)
    cdu_approach: float = 3.0
    dry_approach: float = 6.0         # dry cooler approach to ambient (K)
    free_band: float = 3.0            # partial free-cooling band (K)
    pump_kw: float = 15.0
    loss_frac: float = 0.05           # UPS, distribution, lighting as share of IT
    heat_reuse: bool = False          # supply liquid return heat to district heating in heating months


def cop(t_evap, t_amb, eff=0.5, lift=10.0):
    te = np.asarray(t_evap) + 273.15
    tc = np.asarray(t_amb) + lift + 273.15
    return np.clip(eff * te / np.maximum(tc - te, 1.0), 2.5, 12.0)


def free_frac(t_amb, water_c, approach, band):
    return np.clip((water_c - approach + band - t_amb) / band, 0.0, 1.0)


def run(s: Settings, oat: np.ndarray) -> pd.DataFrame:
    n = len(oat)
    hour = np.arange(n)
    it_var = 1 + 0.03 * np.sin(2 * np.pi * (hour % 24 - 14) / 24)       # small daily IT swing
    it_a, it_b = s.it_air_kw * it_var, s.it_liquid_kw * it_var
    liquid_heat = it_b * s.d2c_capture
    air_heat = it_a + it_b * (1 - s.d2c_capture)

    # Air side
    design_airflow = air_heat.mean() * 1000 / (RHO * CP * 12.0)          # m3/s for 12 K rise
    airflow = design_airflow * s.airflow_oversize * s.fan_speed
    delta_t = air_heat * 1000 / (RHO * CP * airflow)
    rat = s.crah_sat_sp + delta_t
    fan_kw = s.n_crah * s.crah_fan_kw_full * s.fan_speed ** 3 * np.ones(n)

    # Plant
    if s.liquid_on_warm_loop:
        fws = s.tcs_supply - s.cdu_approach
        ff_liq = free_frac(oat, fws, s.dry_approach, s.free_band)
        chw_load = air_heat
    else:
        fws = s.chw_supply
        ff_liq = free_frac(oat, s.chw_supply, s.dry_approach, s.free_band)
        chw_load = air_heat + liquid_heat
    ff_chw = free_frac(oat, s.chw_supply, s.dry_approach, s.free_band)

    if s.liquid_on_warm_loop:
        mech = air_heat * (1 - ff_chw) / cop(s.chw_supply, oat) + liquid_heat * (1 - ff_liq) / cop(fws, oat)
        dry_kw = 0.015 * (air_heat * ff_chw + liquid_heat * ff_liq)
    else:
        mech = chw_load * (1 - ff_chw) / cop(s.chw_supply, oat)
        dry_kw = 0.015 * chw_load * ff_chw
    chiller_kw = mech
    pump_kw = s.pump_kw * np.ones(n)
    losses = s.loss_frac * (it_a + it_b)
    hvac_kw = chiller_kw + dry_kw + pump_kw + fan_kw
    total_kw = it_a + it_b + hvac_kw + losses

    month = np.searchsorted(np.cumsum(HOURS), hour, side="right") + 1
    heating = np.isin(month, [1, 2, 3, 4, 10, 11, 12])
    tcs_return = s.tcs_supply + 10.0
    reuse_kw = np.where(heating & s.heat_reuse & (tcs_return >= 35.0), liquid_heat, 0.0)

    return pd.DataFrame({
        "oat_c": oat, "it_kw": it_a + it_b, "total_kw": total_kw, "hvac_kw": hvac_kw,
        "chiller_kw": chiller_kw, "drycooler_kw": dry_kw, "pump_kw": pump_kw, "crah_fan_kw": fan_kw,
        "crah_sat_c": s.crah_sat_sp * np.ones(n), "crah_rat_c": rat, "crah_fan_pct": 100 * s.fan_speed * np.ones(n),
        "chw_supply_c": s.chw_supply * np.ones(n), "cdu_sws_c": s.tcs_supply * np.ones(n),
        "cdu_rws_c": tcs_return * np.ones(n), "free_cooling_frac": ff_chw, "heat_reuse_kw": reuse_kw,
    })


def summary(df: pd.DataFrame, price_chf_kwh=0.20) -> dict:
    return {
        "PUE": df.total_kw.sum() / df.it_kw.sum(),
        "HVAC_MWh": df.hvac_kw.sum() / 1000,
        "Chiller_MWh": df.chiller_kw.sum() / 1000,
        "Fan_MWh": df.crah_fan_kw.sum() / 1000,
        "HVAC_cost_kCHF": df.hvac_kw.sum() * price_chf_kwh / 1000,
        "Heat_reuse_MWh": df.heat_reuse_kw.sum() / 1000,
    }
