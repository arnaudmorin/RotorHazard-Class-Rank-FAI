''' Class ranking method: FAI '''

import logging
import RHUtils
from eventmanager import Evt
from RHRace import StartBehavior
from Results import RaceClassRankMethod
from RHUI import UIField, UIFieldType, UIFieldSelectOption

#
# @author Arnaud Morin <arnaud.morin@gmail.com>
#
# FAI doc link: https://www.fai.org/sites/default/files/ciam/wcup_drones/sc4_vol_f9_dronesport_24_0.pdf


def initialize(rhapi):
    ranker = FaiRank(rhapi)
    rhapi.events.on(Evt.CLASS_RANK_INITIALIZE, ranker.register_handlers)


class FaiRank():
    """This class handles will do compute a ranking based on FAI rules"""
    def __init__(self, rhapi):
        self.logger = logging.getLogger(__name__)
        self._rhapi = rhapi

    def register_handlers(self, args):
        """Register the "rank" as handler of ranking for classes"""
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
            desc="Qualifying stage used to rank pilots",
        )
        args['register_fn'](
            RaceClassRankMethod(
                "FAI",
                self.rank,
                {'rank-fai-qualifid': 0, 'rank-fai-cta': False},
                [qualifid, cta],
            )
        )

    def rank(self, _, race_class, args):
        """Callback to perform the ranking"""
        meta = {
            'method_label': "FAI",
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

        # Early exit if the qualification bracket is not set in settings
        # 0 is the default, which is wrong
        if args['rank-fai-qualifid'] == 0:
            return [], meta

        # Guess the type of bracket (fai16, etc.)
        bracket_type = self.guess_bracket(race_class.id)

        # If we fail, return early with empty results
        if not bracket_type:
            return [], meta

        q_pilots = []
        try:
            # We first grab the qualification results
            q_r = self._rhapi.db.raceclass_ranking(args['rank-fai-qualifid'])

            for pilot in q_r['ranking']:
                q_pilots.append(pilot['pilot_id'])

            # We add 0 so that we can use that in case of failure finding a pilot (marshall issue, etc.)
            q_pilots.append(0)
        except Exception as e:
            self.logger.error(f'FAI-rank-plugin: failed to grab qualification bracket {e}')
            return [], meta

        # Early exit if don't have any qualification result
        if not q_pilots:
            return [], meta

        # Encapsulate in a big try/catch so any failure won't stop the results cache to be built
        try:
            # Let's build our class rank now
            results = {}
            # Get heats of this class
            # Heats are supposed to be sorted from DB but better safe than sorry
            heats = [heat for heat in sorted(self._rhapi.db.heats_by_class(race_class.id), key=lambda h: h.id)]
            heat_number = 0
            for heat in heats:
                heat_number += 1
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
                            # Handle chase-the-ace (successive final races in FAI doc)
                            # TODO stop using Final, but last of len(heats) instead
                            if heat.name == "Final" and args['rank-fai-cta']:
                                if result['position'] == 1:
                                    new_pilot_result['win'] = 1
                                new_pilot_result['points'] = result['position']
                                if heat_number in results:
                                    for r in results[heat_number].values():
                                        if r['pilot_id'] == pilot.id:
                                            # We increase the point based on the position - this is how FAI does
                                            if result['position']:
                                                new_pilot_result['points'] = r['points'] + result['position']
                                            else:
                                                # If pilot is not having any position, let's add 4
                                                new_pilot_result['points'] = r['points'] + 4
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

                    # Add this result, this may override a previous race that was done
                    # for the same heat ID, but that's fine, we are looping over race in
                    # ordered way so we should have the latest one
                    results[heat_number] = raceresults

            if bracket_type == 'fai64de':
                leaderboard = self.build_leaderboard_fai64de(results, q_pilots)
            if bracket_type == 'fai64':
                leaderboard = self.build_leaderboard_fai64(results, q_pilots)
            if bracket_type == 'fai32de':
                leaderboard = self.build_leaderboard_fai32de(results, q_pilots)
            if bracket_type == 'fai32':
                leaderboard = self.build_leaderboard_fai32(results, q_pilots)
            if bracket_type == 'fai16de':
                leaderboard = self.build_leaderboard_fai16de(results, q_pilots)
            if bracket_type == 'fai16':
                leaderboard = self.build_leaderboard_fai16(results, q_pilots)
            if bracket_type == 'fai8de':
                leaderboard = self.build_leaderboard_fai8de(results, q_pilots)
            if bracket_type == 'fai8':
                leaderboard = self.build_leaderboard_fai8(results, q_pilots)

            # determine ranking
            for i, row in enumerate(leaderboard, start=1):
                pos = i
                row['position'] = pos
        except Exception as e:
            self.logger.error(f'FAI-rank-plugin: failed to rank {e}')
            return [], meta

        return leaderboard, meta

    def build_leaderboard_fai64de(self, results, q_pilots):
        # 9 to 12: 3 and 4 in race 57 and 58
        a = [
            self.try_get_value(results, 57, 3),
            self.try_get_value(results, 57, 4),
            self.try_get_value(results, 58, 3),
            self.try_get_value(results, 58, 4),
        ]

        # 13 to 16: 3 and 4 in race 53 and 54
        b = [
            self.try_get_value(results, 53, 3),
            self.try_get_value(results, 53, 4),
            self.try_get_value(results, 54, 3),
            self.try_get_value(results, 54, 4),
        ]

        # 17 to 24: 3 and 4 in race 49 to 52
        c = [
            self.try_get_value(results, 49, 3),
            self.try_get_value(results, 49, 4),
            self.try_get_value(results, 50, 3),
            self.try_get_value(results, 50, 4),
            self.try_get_value(results, 51, 3),
            self.try_get_value(results, 51, 4),
            self.try_get_value(results, 52, 3),
            self.try_get_value(results, 52, 4),
        ]

        # 25 to 32: 3 and 4 in race 41 to 44
        d = [
            self.try_get_value(results, 41, 3),
            self.try_get_value(results, 41, 4),
            self.try_get_value(results, 42, 3),
            self.try_get_value(results, 42, 4),
            self.try_get_value(results, 43, 3),
            self.try_get_value(results, 43, 4),
            self.try_get_value(results, 44, 3),
            self.try_get_value(results, 44, 4),
        ]

        # 33 to 48: 3 and 4 in race 33 to 40
        e = [
            self.try_get_value(results, 33, 3),
            self.try_get_value(results, 33, 4),
            self.try_get_value(results, 34, 3),
            self.try_get_value(results, 34, 4),
            self.try_get_value(results, 35, 3),
            self.try_get_value(results, 35, 4),
            self.try_get_value(results, 36, 3),
            self.try_get_value(results, 36, 4),
            self.try_get_value(results, 37, 3),
            self.try_get_value(results, 37, 4),
            self.try_get_value(results, 38, 3),
            self.try_get_value(results, 38, 4),
            self.try_get_value(results, 39, 3),
            self.try_get_value(results, 39, 4),
            self.try_get_value(results, 40, 3),
            self.try_get_value(results, 40, 4),
        ]

        # 49 to 64: 3 and 4 in race 25 to 32
        f = [
            self.try_get_value(results, 25, 3),
            self.try_get_value(results, 25, 4),
            self.try_get_value(results, 26, 3),
            self.try_get_value(results, 26, 4),
            self.try_get_value(results, 27, 3),
            self.try_get_value(results, 27, 4),
            self.try_get_value(results, 28, 3),
            self.try_get_value(results, 28, 4),
            self.try_get_value(results, 29, 3),
            self.try_get_value(results, 29, 4),
            self.try_get_value(results, 30, 3),
            self.try_get_value(results, 30, 4),
            self.try_get_value(results, 31, 3),
            self.try_get_value(results, 31, 4),
            self.try_get_value(results, 32, 3),
            self.try_get_value(results, 32, 4),
        ]

        # Sort them based on qualifications
        a = sorted(a, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        b = sorted(b, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        c = sorted(c, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        d = sorted(d, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        e = sorted(e, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        f = sorted(f, key=lambda pilot: q_pilots.index(pilot['pilot_id']))

        # Build our final leaderboard
        return [
            self.try_get_value(results, 62, 1),
            self.try_get_value(results, 62, 2),
            self.try_get_value(results, 62, 3),
            self.try_get_value(results, 62, 4),
            self.try_get_value(results, 61, 3),
            self.try_get_value(results, 61, 4),
            self.try_get_value(results, 59, 3),
            self.try_get_value(results, 59, 4),
            a[0],
            a[1],
            a[2],
            a[3],
            b[0],
            b[1],
            b[2],
            b[3],
            c[0],
            c[1],
            c[2],
            c[3],
            c[4],
            c[5],
            c[6],
            c[7],
            d[0],
            d[1],
            d[2],
            d[3],
            d[4],
            d[5],
            d[6],
            d[7],
            e[0],
            e[1],
            e[2],
            e[3],
            e[4],
            e[5],
            e[6],
            e[7],
            e[8],
            e[9],
            e[10],
            e[11],
            e[12],
            e[13],
            e[14],
            e[15],
            f[0],
            f[1],
            f[2],
            f[3],
            f[4],
            f[5],
            f[6],
            f[7],
            f[8],
            f[9],
            f[10],
            f[11],
            f[12],
            f[13],
            f[14],
            f[15],
        ]

    def build_leaderboard_fai64(self, results, q_pilots):
        # 9 to 16: 3 and 4 in race 25 to 28
        a = [
            self.try_get_value(results, 25, 3),
            self.try_get_value(results, 25, 4),
            self.try_get_value(results, 26, 3),
            self.try_get_value(results, 26, 4),
            self.try_get_value(results, 27, 3),
            self.try_get_value(results, 27, 4),
            self.try_get_value(results, 28, 3),
            self.try_get_value(results, 28, 4),
        ]

        # 17 to 32: 3 and 4 in race 17 to 24
        b = [
            self.try_get_value(results, 17, 3),
            self.try_get_value(results, 17, 4),
            self.try_get_value(results, 18, 3),
            self.try_get_value(results, 18, 4),
            self.try_get_value(results, 19, 3),
            self.try_get_value(results, 19, 4),
            self.try_get_value(results, 20, 3),
            self.try_get_value(results, 20, 4),
            self.try_get_value(results, 21, 3),
            self.try_get_value(results, 21, 4),
            self.try_get_value(results, 22, 3),
            self.try_get_value(results, 22, 4),
            self.try_get_value(results, 23, 3),
            self.try_get_value(results, 23, 4),
            self.try_get_value(results, 24, 3),
            self.try_get_value(results, 24, 4),
        ]

        # 33 to 64: 3 and 4 in race 1 to 16
        c = [
            self.try_get_value(results, 1, 3),
            self.try_get_value(results, 1, 4),
            self.try_get_value(results, 2, 3),
            self.try_get_value(results, 2, 4),
            self.try_get_value(results, 3, 3),
            self.try_get_value(results, 3, 4),
            self.try_get_value(results, 4, 3),
            self.try_get_value(results, 4, 4),
            self.try_get_value(results, 5, 3),
            self.try_get_value(results, 5, 4),
            self.try_get_value(results, 6, 3),
            self.try_get_value(results, 6, 4),
            self.try_get_value(results, 7, 3),
            self.try_get_value(results, 7, 4),
            self.try_get_value(results, 8, 3),
            self.try_get_value(results, 8, 4),
            self.try_get_value(results, 9, 3),
            self.try_get_value(results, 9, 4),
            self.try_get_value(results, 10, 3),
            self.try_get_value(results, 10, 4),
            self.try_get_value(results, 11, 3),
            self.try_get_value(results, 11, 4),
            self.try_get_value(results, 12, 3),
            self.try_get_value(results, 12, 4),
            self.try_get_value(results, 13, 3),
            self.try_get_value(results, 13, 4),
            self.try_get_value(results, 14, 3),
            self.try_get_value(results, 14, 4),
            self.try_get_value(results, 15, 3),
            self.try_get_value(results, 15, 4),
            self.try_get_value(results, 16, 3),
            self.try_get_value(results, 16, 4),
        ]

        # Sort them based on qualifications
        a = sorted(a, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        b = sorted(b, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        c = sorted(c, key=lambda pilot: q_pilots.index(pilot['pilot_id']))

        # Build our final leaderboard
        return [
            self.try_get_value(results, 32, 1),
            self.try_get_value(results, 32, 2),
            self.try_get_value(results, 32, 3),
            self.try_get_value(results, 32, 4),
            self.try_get_value(results, 31, 1),
            self.try_get_value(results, 31, 2),
            self.try_get_value(results, 31, 3),
            self.try_get_value(results, 31, 4),
            a[0],
            a[1],
            a[2],
            a[3],
            a[4],
            a[5],
            a[6],
            a[7],
            b[0],
            b[1],
            b[2],
            b[3],
            b[4],
            b[5],
            b[6],
            b[7],
            b[8],
            b[9],
            b[10],
            b[11],
            b[12],
            b[13],
            b[14],
            b[15],
            c[0],
            c[1],
            c[2],
            c[3],
            c[4],
            c[5],
            c[6],
            c[7],
            c[8],
            c[9],
            c[10],
            c[11],
            c[12],
            c[13],
            c[14],
            c[15],
            c[16],
            c[17],
            c[18],
            c[19],
            c[20],
            c[21],
            c[22],
            c[23],
            c[24],
            c[25],
            c[26],
            c[27],
            c[28],
            c[29],
            c[30],
            c[31],
        ]

    def build_leaderboard_fai32de(self, results, q_pilots):
        # 9 to 12: 3 and 4 in race 25 and 26
        a = [
            self.try_get_value(results, 25, 3),
            self.try_get_value(results, 25, 4),
            self.try_get_value(results, 26, 3),
            self.try_get_value(results, 26, 4),
        ]

        # 13 to 16: 3 and 4 in race 21 and 22
        b = [
            self.try_get_value(results, 21, 3),
            self.try_get_value(results, 21, 4),
            self.try_get_value(results, 22, 3),
            self.try_get_value(results, 22, 4),
        ]

        # 17 to 24: 3 and 4 in race 17 to 20
        c = [
            self.try_get_value(results, 17, 3),
            self.try_get_value(results, 17, 4),
            self.try_get_value(results, 18, 3),
            self.try_get_value(results, 18, 4),
            self.try_get_value(results, 19, 3),
            self.try_get_value(results, 19, 4),
            self.try_get_value(results, 20, 3),
            self.try_get_value(results, 20, 4),
        ]

        # 25 to 32: 3 and 4 in race 13 to 16
        d = [
            self.try_get_value(results, 13, 3),
            self.try_get_value(results, 13, 4),
            self.try_get_value(results, 14, 3),
            self.try_get_value(results, 14, 4),
            self.try_get_value(results, 15, 3),
            self.try_get_value(results, 15, 4),
            self.try_get_value(results, 16, 3),
            self.try_get_value(results, 16, 4),
        ]

        # Sort them based on qualifications
        a = sorted(a, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        b = sorted(b, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        c = sorted(c, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        d = sorted(d, key=lambda pilot: q_pilots.index(pilot['pilot_id']))

        # Build our final leaderboard
        return [
            self.try_get_value(results, 30, 1),
            self.try_get_value(results, 30, 2),
            self.try_get_value(results, 30, 3),
            self.try_get_value(results, 30, 4),
            self.try_get_value(results, 29, 3),
            self.try_get_value(results, 29, 4),
            self.try_get_value(results, 27, 3),
            self.try_get_value(results, 27, 4),
            a[0],
            a[1],
            a[2],
            a[3],
            b[0],
            b[1],
            b[2],
            b[3],
            c[0],
            c[1],
            c[2],
            c[3],
            c[4],
            c[5],
            c[6],
            c[7],
            d[0],
            d[1],
            d[2],
            d[3],
            d[4],
            d[5],
            d[6],
            d[7],
        ]

    def build_leaderboard_fai32(self, results, q_pilots):
        # 9 to 16: 3 and 4 in race 9 to 12
        a = [
            self.try_get_value(results, 9, 3),
            self.try_get_value(results, 9, 4),
            self.try_get_value(results, 10, 3),
            self.try_get_value(results, 10, 4),
            self.try_get_value(results, 11, 3),
            self.try_get_value(results, 11, 4),
            self.try_get_value(results, 12, 3),
            self.try_get_value(results, 12, 4),
        ]

        # 17 to 32: 3 and 4 in race 1 to 8
        b = [
            self.try_get_value(results, 1, 3),
            self.try_get_value(results, 1, 4),
            self.try_get_value(results, 2, 3),
            self.try_get_value(results, 2, 4),
            self.try_get_value(results, 3, 3),
            self.try_get_value(results, 3, 4),
            self.try_get_value(results, 4, 3),
            self.try_get_value(results, 4, 4),
            self.try_get_value(results, 5, 3),
            self.try_get_value(results, 5, 4),
            self.try_get_value(results, 6, 3),
            self.try_get_value(results, 6, 4),
            self.try_get_value(results, 7, 3),
            self.try_get_value(results, 7, 4),
            self.try_get_value(results, 8, 3),
            self.try_get_value(results, 8, 4),
        ]

        # Sort them based on qualifications
        a = sorted(a, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        b = sorted(b, key=lambda pilot: q_pilots.index(pilot['pilot_id']))

        # Build our final leaderboard
        return [
            self.try_get_value(results, 16, 1),
            self.try_get_value(results, 16, 2),
            self.try_get_value(results, 16, 3),
            self.try_get_value(results, 16, 4),
            self.try_get_value(results, 15, 1),
            self.try_get_value(results, 15, 2),
            self.try_get_value(results, 15, 3),
            self.try_get_value(results, 15, 4),
            a[0],
            a[1],
            a[2],
            a[3],
            a[4],
            a[5],
            a[6],
            a[7],
            b[0],
            b[1],
            b[2],
            b[3],
            b[4],
            b[5],
            b[6],
            b[7],
            b[8],
            b[9],
            b[10],
            b[11],
            b[12],
            b[13],
            b[14],
            b[15],
        ]

    def build_leaderboard_fai16de(self, results, q_pilots):
        # 9 to 12: 3 and 4 in race 9 and 10
        a = [
            self.try_get_value(results, 10, 3),
            self.try_get_value(results, 10, 4),
            self.try_get_value(results, 9, 3),
            self.try_get_value(results, 9, 4),
        ]
        # 13 to 16: 3 and 4 in race 5 and 6
        b = [
            self.try_get_value(results, 6, 3),
            self.try_get_value(results, 6, 4),
            self.try_get_value(results, 5, 3),
            self.try_get_value(results, 5, 4),
        ]

        # Sort them based on qualifications
        a = sorted(a, key=lambda pilot: q_pilots.index(pilot['pilot_id']))
        b = sorted(b, key=lambda pilot: q_pilots.index(pilot['pilot_id']))

        # Build our final leaderboard
        return [
            self.try_get_value(results, 14, 1),
            self.try_get_value(results, 14, 2),
            self.try_get_value(results, 14, 3),
            self.try_get_value(results, 14, 4),
            self.try_get_value(results, 13, 3),
            self.try_get_value(results, 13, 4),
            self.try_get_value(results, 11, 3),
            self.try_get_value(results, 11, 4),
            a[0],
            a[1],
            a[2],
            a[3],
            b[0],
            b[1],
            b[2],
            b[3],
        ]

    def build_leaderboard_fai16(self, results, q_pilots):
        # 9 to 16: 3 and 4 in race 1 to 4
        a = [
            self.try_get_value(results, 1, 3),
            self.try_get_value(results, 1, 4),
            self.try_get_value(results, 2, 3),
            self.try_get_value(results, 2, 4),
            self.try_get_value(results, 3, 3),
            self.try_get_value(results, 3, 4),
            self.try_get_value(results, 4, 3),
            self.try_get_value(results, 4, 4),
        ]

        # Sort them based on qualifications
        a = sorted(a, key=lambda pilot: q_pilots.index(pilot['pilot_id']))

        # Build our final leaderboard
        return [
            self.try_get_value(results, 8, 1),
            self.try_get_value(results, 8, 2),
            self.try_get_value(results, 8, 3),
            self.try_get_value(results, 8, 4),
            self.try_get_value(results, 7, 1),
            self.try_get_value(results, 7, 2),
            self.try_get_value(results, 7, 3),
            self.try_get_value(results, 7, 4),
            a[0],
            a[1],
            a[2],
            a[3],
            a[4],
            a[5],
            a[6],
            a[7],
        ]

    def build_leaderboard_fai8de(self, results, q_pilots):
        """These are not official in FAI but that's great to have it"""
        # Build our final leaderboard
        return [
            self.try_get_value(results, 6, 1),
            self.try_get_value(results, 6, 2),
            self.try_get_value(results, 6, 3),
            self.try_get_value(results, 6, 4),
            self.try_get_value(results, 5, 3),
            self.try_get_value(results, 5, 4),
            self.try_get_value(results, 3, 3),
            self.try_get_value(results, 3, 4),
        ]

    def build_leaderboard_fai8(self, results, q_pilots):
        """These are not official in FAI but that's great to have it"""
        # Build our final leaderboard
        return [
            self.try_get_value(results, 4, 1),
            self.try_get_value(results, 4, 2),
            self.try_get_value(results, 4, 3),
            self.try_get_value(results, 4, 4),
            self.try_get_value(results, 3, 1),
            self.try_get_value(results, 3, 2),
            self.try_get_value(results, 3, 3),
            self.try_get_value(results, 3, 4),
        ]

    def guess_bracket(self, class_id):
        """Guess the size of the bracket:
            fai64de: 62 heats
            fai64: 32 heats
            fai32de: 30 heats
            fai32: 16 heats
            fai16de: 14 heats
            fai16: 8 heats
            fai8de: 6 heats
            fai8: 4 heats
        """
        s = {
            62: 'fai64de',
            32: 'fai64',
            30: 'fai32de',
            16: 'fai32',
            14: 'fai16de',
            8: 'fai16',
            6: 'fai8de',
            4: 'fai8',
        }

        n = len(self._rhapi.db.heats_by_class(class_id))
        try:
            return s[n]
        except KeyError:
            return None

    def try_get_value(self, f, k, s):
        try:
            return f[k][s]
        except Exception:
            # We use pilot_id 0 with no callsign
            # Pilot 0 is added at the end of qualif as the last pilot
            return {'pilot_id': 0, 'callsign': ''}
