import os
import json
BASEDIR = os.getcwd()
DB_FILE_NAME = 'database.db'
_DB_URI = 'sqlite:///' + os.path.join(BASEDIR, DB_FILE_NAME)

import RHUtils
import Database
from Database import ProgramMethod, HeatStatus
Database.initialize(_DB_URI)

# This is a big copy paste from heat_automation that I amended to fit my needs
# This is needed to seed heats correctly
def calc_heat_pilots(heat):
    # skip if heat status confirmed
    if (heat.status == HeatStatus.CONFIRMED):
        return

    slots = Database.HeatNode.query.filter_by(heat_id=heat.id).all()
    for slot in slots:
        if slot.method == ProgramMethod.NONE:
            slot.pilot_id = RHUtils.PILOT_ID_NONE

        elif slot.method == ProgramMethod.HEAT_RESULT:
            if slot.seed_id:
                if slot.seed_rank:
                    seed_heat = Database.Heat.query.filter_by(id=slot.seed_id).first()
                    print(f'For heat {heat.name} looking rank in {seed_heat.name}')
                    # We are supposed to take the result, but we dont have it (painful to build it)
                    # Anyway, our lowest pilot id is always first, let's fake this
                    heatnodes = Database.HeatNode.query.filter_by(heat_id=seed_heat.id).all()
                    pilots = sorted([x.pilot_id for x in heatnodes if x.pilot_id != 0])
                    print(f'Found pilots: {pilots}')
                    slot.pilot_id = pilots[slot.seed_rank - 1]
        elif slot.method == ProgramMethod.CLASS_RESULT:
            if slot.seed_id:
                if slot.seed_rank:
                    seed_class = Database.RaceClass.query.filter_by(id=slot.seed_id).first()
                    positions = seed_class.ranking['ranking']
                    slot.pilot_id = positions[slot.seed_rank - 1]['pilot_id']
        Database.DB_session.commit()
        Database.DB_session.flush()

for heat in Database.Heat.query.all():
    #print(json.dumps(heat.results))
    #import sys
    #sys.exit(0)

    if heat.class_id == 1:
        # TODO tmp
        continue
        pass
    else:
        calc_heat_pilots(heat)

    if heat.status == 0:
        heat.status = 2
        Database.DB_session.flush()
        Database.DB_session.commit()

    # Look for pilots from HeatNode
    # Grab heatNode
    heatnodes = Database.HeatNode.query.filter_by(heat_id=heat.id).all()
    for heatnode in heatnodes:
        pilot = Database.Pilot.query.get(heatnode.pilot_id)
        if not pilot:
            continue
        laps = [
            1000.0,
            (1+pilot.id) * 1000.0,
            (1+pilot.id) * 1000.0 + 1000.0,
            (1+pilot.id) * 1000.0 + 2000.0,
        ]
        pilotrace = Database.SavedPilotRace.query.filter_by(race_id=heat.id, pilot_id=pilot.id).first()
        if not pilotrace:
            # Grab heatNode
            pilotrace = Database.SavedPilotRace(
                race_id=heat.id,
                node_index=heatnode.node_index,
                pilot_id=pilot.id,
                history_values='[]',
                history_times='[]',
                penalty_time=0,
                enter_at=64,
                exit_at=64,
                frequency=5732,
            )

            Database.DB_session.add(pilotrace)
            Database.DB_session.flush()
            Database.DB_session.refresh(pilotrace)

        for lap in laps:
            Database.DB_session.add(Database.SavedRaceLap(
                race_id=heat.id,
                pilotrace_id=pilotrace.id,
                node_index=pilotrace.node_index,
                pilot_id=pilot.id,
                lap_time_stamp=lap,
                lap_time=lap,
                lap_time_formatted=f'{lap}',
                source=1,
                deleted=0
            ))
            Database.DB_session.commit()
            Database.DB_session.flush()

    Database.DB_session.add(Database.SavedRaceMeta(
        round_id=1,
        heat_id=heat.id,
        class_id=heat.class_id,
        format_id=13,
        start_time=318138.80385854106862,
        start_time_formatted='2024-09-13 00:19:26.962',
        _cache_status=json.dumps({
            'data_ver': 318144.498885659,
            'build_ver': None
        })
    ))
    Database.DB_session.commit()
    
