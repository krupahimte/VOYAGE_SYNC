#!/usr/bin/env bash
# The single held-out run (Phase 10), executed on 2026-11-02 against the frozen pre-registration
# (sha256 d2bd96c9808a15423fe408182cc9d50da4b4d13bb49b5fdae36748636bf57184).
# One logical run split over parallel processes by catalogue x drift x trip group; every seed is keyed by
# its scenario, so the rows equal those of a single process (checked on dev before launch).
#
# Launch history:
#   1st launch: the trip-group array was named GROUPS, a read-only bash built-in, so every main process got
#               a group ID instead of trip names and ran 0 scenarios (its outputs were empty and were
#               deleted; no held-out main result existed). The four E2' processes, which do not use the
#               array, ran correctly then and are kept: eval/results_v3/heldout/e2_prime/.
#   2nd launch: this script, main processes only.
set -u
cd "$(dirname "$0")/../../.."
PY=backend/.venv/Scripts/python.exe
OUT=eval/results_v3/heldout
TRIP_GROUPS=(
  "paris_couple_weekend,barcelona_friends_summer,istanbul_solo_culture,singapore_family_city,new_york_couple_luxury"
  "bangkok_solo_budget,athens_family_history,amsterdam_solo_short,sydney_couple_long,london_group_theatre"
  "mumbai_business_domestic,delhi_family_domestic,goa_friends_domestic,jaipur_couple_domestic,tokyo_couple_short"
  "rome_group_long,dubai_family_winter,lisbon_couple_food,bali_solo_wellness,kyoto_couple_autumn"
)
mkdir -p "$OUT/logs"
for cat in realistic stress; do
  for drift in on off; do
    for g in 0 1 2 3; do
      $PY eval/experiments.py --split heldout --catalogue $cat --drift $drift --seeds 20 --beam-width 10 \
        --trips "${TRIP_GROUPS[$g]}" --out "$OUT/main/${cat}_${drift}_g$g" > "$OUT/logs/main_${cat}_${drift}_g$g.log" 2>&1 &
    done
    # E2' (the H3 comparison), run in the 1st launch:
    # $PY eval/experiments.py --split heldout --catalogue $cat --drift $drift --arms E2 --seeds 1 --beam-width 50 \
    #   --out "$OUT/e2_prime/${cat}_${drift}" > "$OUT/logs/e2prime_${cat}_${drift}.log" 2>&1 &
  done
done
wait
echo "held-out run finished"
missing=$(grep -L "^wrote" "$OUT"/logs/main_*.log)
if [ -n "$missing" ]; then echo "processes without a completion line: $missing"; else echo "all 16 main processes completed"; fi
