''' Class ranking method: FAI16DE '''

import logging
import RHUtils
from eventmanager import Evt
from RHRace import StartBehavior
from Results import RaceClassRankMethod
from RHUI import UIField, UIFieldType, UIFieldSelectOption

#
# @author Arnaud Morin <arnaud.morin@gmail.com>
#


def initialize(rhapi):
    ranker = Fai16deRank(rhapi)
    rhapi.events.on(Evt.CLASS_RANK_INITIALIZE, ranker.register_handlers)


class Fai16deRank():
    """This class handles will do FAI 16 pilots Double Elim ranking"""
    def __init__(self, rhapi):
        self.logger = logging.getLogger(__name__)
        self._rhapi = rhapi

    def rank(self, _, race_class, args):
        self.logger.info(args)
        meta = {
            'method_label': "FAI 16 pilots Double Elimination",
            'rank_fields': [
                {
                    'name': 'position',
                    'label': "Position"
                },
                {
                    'name': 'points',
                    'label': "Points"
                },
            ]
        }

        try:
            # We first grab the qualification results
            q_r = self._rhapi.db.raceclass_ranking(args['rank-fai-qualifid'])
            q_pilots = []

            for pilot in q_r['ranking']:
                q_pilots.append(pilot['pilot_id'])

            # We add 0 so that we can use that in case of failure finding a pilot (marshall issue, etc.)
            q_pilots.append(0)

            # Let's build our class rank now
            results = {}
            # Get heats of this class
            heats = self._rhapi.db.heats_by_class(race_class.id)
            for heat in heats:
                races = self._rhapi.db.races_by_heat(heat.id)

                for race in races:
                    raceresults = {}
                    # Grab the race result
                    r = self._rhapi.db.race_results(race.id)
                    if r != None:
                        # What is important for us is position more than laps
                        # Take only the results that are used to make progress
                        filteredresults = r[r["meta"]["primary_leaderboard"]]

                        for result in filteredresults:
                            pilot = self._rhapi.db.pilot_by_id(result['pilot_id'])
                            new_pilot_result = {
                                'pilot_id': pilot.id,
                                'callsign': pilot.callsign,
                                'win': 0,
                                'points': 0,
                            }
                            # TODO handle the Final
                            if heat.name == "Final" and args['rank-fai-cta']:
                                if result['position'] == 1:
                                    new_pilot_result['win'] = 1
                                new_pilot_result['points'] = result['position']
                                if heat.name in results:
                                    for r in results[heat.name].values():
                                        if r['pilot_id'] == pilot.id:
                                            # We increase the point based on the position - this is how FAI does
                                            new_pilot_result['points'] = r['points'] + result['position']
                                            if result['position'] == 1:
                                                new_pilot_result['win'] = r['win'] + 1
                            raceresults[result['position']] = new_pilot_result

                        # Let's sort raceresults for Final
                        if heat.name == "Final" and args['rank-fai-cta']:
                            # Sort by keeping first the one that wone twice
                            # then by increasing points
                            # We also need to sort by qualifying stage if two of them are deuce
                            sorted_raceresults = sorted(
                                raceresults.values(),
                                key=lambda x: (x['win'] != 2, x['points'], q_pilots.index(x['pilot_id']))
                            )
                            # Rebuild our dict
                            raceresults = {
                                1: sorted_raceresults[0],
                                2: sorted_raceresults[1],
                                3: sorted_raceresults[2],
                                4: sorted_raceresults[3],
                            }
                            self.logger.info(raceresults)

                    # Add this result, this may override a previous race that was done
                    # for the same heat ID, but that's fine, we are looping over race in
                    # ordered way so we should have the latest one
                    results[heat.name] = raceresults

            nine_pilots_sorted = []
            thirteen_pilots_sorted = []
            if q_pilots:
                # 9 to 12: 3 and 4 in race 9 and 10 based on qualif
                nine_pilots = [
                    self.try_get_value(results, 'Race 10', 3),
                    self.try_get_value(results, 'Race 10', 4),
                    self.try_get_value(results, 'Race 9', 3),
                    self.try_get_value(results, 'Race 9', 4),
                ]
                # 13 to 16: 3 and 4 in race 5 and 6 based on qualif
                thirteen_pilots = [
                    self.try_get_value(results, 'Race 6', 3),
                    self.try_get_value(results, 'Race 6', 4),
                    self.try_get_value(results, 'Race 5', 3),
                    self.try_get_value(results, 'Race 5', 4),
                ]

                # Sort them based on qualifications
                nine_pilots_sorted = sorted(nine_pilots, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
                thirteen_pilots_sorted = sorted(thirteen_pilots, key=lambda pilot: q_pilots.index(pilot['pilot_id']))

            # Build our final leaderboard
            leaderboard = [
                self.try_get_value(results, 'Final', 1),
                self.try_get_value(results, 'Final', 2),
                self.try_get_value(results, 'Final', 3),
                self.try_get_value(results, 'Final', 4),
                self.try_get_value(results, 'Race 13', 3),
                self.try_get_value(results, 'Race 13', 4),
                self.try_get_value(results, 'Race 11', 3),
                self.try_get_value(results, 'Race 11', 4),
                nine_pilots_sorted[0],
                nine_pilots_sorted[1],
                nine_pilots_sorted[2],
                nine_pilots_sorted[3],
                thirteen_pilots_sorted[0],
                thirteen_pilots_sorted[1],
                thirteen_pilots_sorted[2],
                thirteen_pilots_sorted[3],
            ]

            self.logger.info(q_pilots)

            # determine ranking
            for i, row in enumerate(leaderboard, start=1):
                pos = i
                row['position'] = pos
        except Exception as e:
            self.logger.error(f'Failed to rank FAI16DE {e}')
            raise
            return [], meta

        return leaderboard, meta

    def try_get_value(self, f, k, s):
        if k in f and s in f[k]:
            return f[k][s]
        return {'pilot_id': 0, 'callsign': ''}

    def register_handlers(self, args):
        # Add some options
        cta = UIField(
            name='rank-fai-cta',
            label='Final with Chase The Ace',
            field_type=UIFieldType.CHECKBOX,
            desc="Whether the Final race will be done with Chase the Ace mode or not.",
        )

        classes = self._rhapi.db.raceclasses
        options = []
        for c in classes:
            if not c.name:
                name = f"Class {c.id}"
            else:
                name = c.name
            options.append(UIFieldSelectOption(c.id,name))
        qualifid = UIField(
            name='rank-fai-qualifid',
            label='Qualification Class',
            field_type=UIFieldType.SELECT,
            options=options,
            desc="Qualifying stage used to rank pilots from 9 to 16",
        )
        args['register_fn'](
            RaceClassRankMethod(
                "FAI 16 pilots Double Elimination",
                self.rank,
                {'rank-fai-qualifid': 1, 'rank-fai-cta': False},
                [qualifid, cta],
            )
        )

