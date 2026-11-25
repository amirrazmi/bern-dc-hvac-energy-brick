"""
Build a Brick Schema model of a representative 1 MW data centre in the Bern region.

Hall A: air-cooled (4 CRAH units on the chilled water loop)
Hall B: high-density racks with direct-to-chip liquid cooling via 2 CDUs
Plant:  2 chillers, 2 dry coolers (free cooling), 2 pumps, chilled water loop
Meters: building electrical meter, IT meter, HVAC meter, outside air sensor

All site data are assumptions for a concept case study.
"""
from rdflib import Graph, Namespace, Literal, RDF, RDFS, XSD

BRICK = Namespace("https://brickschema.org/schema/Brick#")
BLDG = Namespace("urn:bern-dc#")
DC = Namespace("urn:bern-dc-props#")   # simple custom properties for ratings and setpoints


def build() -> Graph:
    g = Graph()
    g.bind("brick", BRICK); g.bind("bldg", BLDG); g.bind("dc", DC)

    def add(node, cls, label):
        g.add((node, RDF.type, BRICK[cls])); g.add((node, RDFS.label, Literal(label)))
        return node

    def point(parent, name, cls, ts_col):
        p = add(BLDG[name], cls, name.replace("_", " "))
        g.add((parent, BRICK.hasPoint, p)); g.add((p, DC.timeseries, Literal(ts_col)))
        return p

    site = add(BLDG.Bern_DC, "Building", "Bern data centre (representative case)")
    g.add((site, DC.itCapacity_kW, Literal(1000, datatype=XSD.integer)))
    floor = add(BLDG.Level_1, "Floor", "Level 1"); g.add((site, BRICK.hasPart, floor))
    hall_a = add(BLDG.Hall_A, "Server_Room", "Hall A, air-cooled, 400 kW IT")
    hall_b = add(BLDG.Hall_B, "Server_Room", "Hall B, liquid-cooled, 600 kW IT")
    plant = add(BLDG.Plant_Room, "Mechanical_Room", "Cooling plant room")
    for r in (hall_a, hall_b, plant): g.add((floor, BRICK.hasPart, r))
    g.add((hall_a, DC.itLoad_kW, Literal(400))); g.add((hall_b, DC.itLoad_kW, Literal(600)))

    # Chilled water plant
    chw = add(BLDG.CHW_Loop, "Chilled_Water_Loop", "Chilled water loop")
    point(chw, "CHW_Supply_Temp", "Chilled_Water_Supply_Temperature_Sensor", "chw_supply_c")
    for i in (1, 2):
        ch = add(BLDG[f"Chiller_{i}"], "Chiller", f"Chiller {i}")
        g.add((ch, BRICK.feeds, chw)); g.add((ch, DC.ratedCooling_kW, Literal(600)))
        point(ch, f"Chiller_{i}_Power", "Electric_Power_Sensor", f"chiller{i}_kw")
        dcl = add(BLDG[f"Dry_Cooler_{i}"], "Dry_Cooler", f"Dry cooler {i} (free cooling)")
        g.add((dcl, BRICK.feeds, chw))
        point(dcl, f"Dry_Cooler_{i}_Power", "Electric_Power_Sensor", f"drycooler{i}_kw")
        pu = add(BLDG[f"CHW_Pump_{i}"], "Pump", f"Chilled water pump {i}")
        g.add((chw, BRICK.hasPart, pu))
        point(pu, f"CHW_Pump_{i}_Power", "Electric_Power_Sensor", f"pump{i}_kw")

    # Hall A: CRAH units
    zone_a = add(BLDG.Zone_Hall_A, "HVAC_Zone", "Cold aisle zone, Hall A"); g.add((zone_a, BRICK.hasLocation, hall_a))
    for i in range(1, 5):
        c = add(BLDG[f"CRAH_A{i}"], "CRAH", f"CRAH A{i}")
        g.add((chw, BRICK.feeds, c)); g.add((c, BRICK.feeds, zone_a)); g.add((c, BRICK.hasLocation, hall_a))
        f = add(BLDG[f"CRAH_A{i}_Fan"], "Fan", f"CRAH A{i} fan"); g.add((c, BRICK.hasPart, f))
        point(c, f"CRAH_A{i}_Supply_Air_Temp", "Supply_Air_Temperature_Sensor", f"crah{i}_sat_c")
        point(c, f"CRAH_A{i}_Return_Air_Temp", "Return_Air_Temperature_Sensor", f"crah{i}_rat_c")
        sp = point(c, f"CRAH_A{i}_Supply_Air_Temp_SP", "Supply_Air_Temperature_Setpoint", f"crah{i}_sat_sp")
        point(f, f"CRAH_A{i}_Fan_Speed", "Fan_Speed_Command", f"crah{i}_fan_pct")
        point(c, f"CRAH_A{i}_Power", "Electric_Power_Sensor", f"crah{i}_kw")

    # Hall B: CDUs (modelled as heat exchangers) for direct-to-chip liquid cooling
    for i in (1, 2):
        cdu = add(BLDG[f"CDU_B{i}"], "Heat_Exchanger", f"CDU B{i} (coolant distribution unit)")
        g.add((chw, BRICK.feeds, cdu)); g.add((cdu, BRICK.hasLocation, hall_b))
        point(cdu, f"CDU_B{i}_Supply_Water_Temp", "Supply_Water_Temperature_Sensor", f"cdu{i}_sws_c")
        point(cdu, f"CDU_B{i}_Return_Water_Temp", "Return_Water_Temperature_Sensor", f"cdu{i}_rws_c")

    # Meters and weather
    point(site, "Outside_Air_Temp", "Outside_Air_Temperature_Sensor", "oat_c")
    m_tot = add(BLDG.Meter_Total, "Building_Electrical_Meter", "Total facility meter"); g.add((site, BRICK.hasPart, m_tot))
    point(m_tot, "Total_Power", "Electric_Power_Sensor", "total_kw")
    m_it = add(BLDG.Meter_IT, "Electrical_Meter", "IT load meter"); g.add((site, BRICK.hasPart, m_it))
    point(m_it, "IT_Power", "Electric_Power_Sensor", "it_kw")
    return g


if __name__ == "__main__":
    g = build()
    g.serialize("model/bern_dc.ttl", format="turtle")
    print(f"Brick model written: {len(g)} triples")
