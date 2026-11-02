# Labelling briefs (ground truth v3)

Read `LABELLING_PROTOCOL.md` first. Each trip is shown **before** any disruption.

## santorini_solo_luxury

Mumbai → Santorini, 2027-06-14 – 2027-06-19, 1 traveller(s), budget 4000 USD.

### Tasks (planned)

| Task | Plan | Other options the planner may choose |
|---|---|---|
| `flight_outbound` | VY280 BOM→JTR 08:00–14:00, 560.0 USD | SK317 BOM→JTR 11:30–17:30, 627.2 USD<br>ZZ354 BOM→JTR 06:00–14:00, 700.0 USD |
| `flight_return` | ZZ190 JTR→BOM 16:00–22:00, 310.0 USD | VY227 JTR→BOM 09:00–15:00, 341.0 USD<br>SK264 JTR→BOM 19:30–02:30, 372.0 USD |
| `weather_day_1` | 2027-06-14: sunny | — |
| `weather_day_2` | 2027-06-15: sunny | — |
| `weather_day_3` | 2027-06-16: sunny | — |
| `weather_day_4` | 2027-06-17: sunny | — |
| `weather_day_5` | 2027-06-18: partly_cloudy | — |
| `hotel_stay` | Santorini Central Inn, 2027-06-14 → 2027-06-19, check-in until 23:59, 1908.0 USD | Santorini Vista Suites, 2027-06-14 → 2027-06-19, check-in until 23:59, 1926.0 USD<br>Santorini Harbour Hotel, 2027-06-14 → 2027-06-19, check-in until 23:59, 1908.0 USD |
| `airport_transfer_in` | pickup 14:30, drop-off 14:40 (10 min) | pickup 14:15, drop-off 14:25 (10 min)<br>pickup 14:45, drop-off 14:55 (10 min)<br>pickup 15:00, drop-off 15:10 (10 min) |
| `day_2_plan` | 2027-06-15: 09:30 Artisan Market; 12:30 Lunch at Sea Salt Bistro; 14:30 Corner Roastery; 18:00 Sunset at Castle Ruins (outdoor); 20:00 Dinner at Harbour Grill | re-fillable with other nearby places |
| `day_3_plan` | 2027-06-16: 09:30 Pebble Cove (outdoor); 12:30 Lunch at The Fig Tree; 14:30 Stillwater Spa; 18:00 Sunset at Santorini Old Town (outdoor); 20:00 Dinner at Harbour Grill | re-fillable with other nearby places |
| `day_4_plan` | 2027-06-17: 09:30 Old Bazaar; 12:30 Lunch at Harbour Grill; 14:30 Morning Light Café; 18:00 Sunset at Clifftop Chapel (outdoor); 20:00 Dinner at Sea Salt Bistro | re-fillable with other nearby places |
| `day_5_plan` | 2027-06-18: 09:30 Long Beach (outdoor); 12:30 Lunch at Blue Door Kitchen; 14:30 Stillwater Spa; 18:00 Sunset at Clifftop Chapel (outdoor); 20:00 Dinner at Olive & Vine | re-fillable with other nearby places |
| `day_1_plan` | 2027-06-14: 18:00 Sunset at Clifftop Chapel (outdoor); 20:00 Dinner at Harbour Grill | re-fillable with other nearby places |
| `airport_transfer_out` | pickup 12:50, drop-off 13:00 (10 min) | pickup 13:20, drop-off 13:30 (10 min)<br>pickup 12:20, drop-off 12:30 (10 min)<br>pickup 11:50, drop-off 12:00 (10 min) |
| `budget_check` | total 3431.74 of 4000.0 USD | — |

### Dependencies (each must hold after a repair)

- `flight_outbound` → `hotel_stay`: arrival + 90 min ≤ check-in cutoff (FLIGHT_BEFORE_CHECKIN)
- `flight_outbound` → `airport_transfer_in`: pickup 15–90 min after arrival (TRANSFER_AFTER_FLIGHT)
- `hotel_stay` → `airport_transfer_in`: transfer is routed to the booked hotel (TRANSFER_SERVES_HOTEL)
- `hotel_stay` → `airport_transfer_out`: transfer is routed to the booked hotel (TRANSFER_SERVES_HOTEL)
- `airport_transfer_in` → `day_1_plan`: reach the hotel before day 1 starts (TRANSFER_BEFORE_DAY_START)
- `hotel_stay` → `day_1_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_1` → `day_1_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_2_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_2` → `day_2_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_3_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_3` → `day_3_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_4_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_4` → `day_4_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_5_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_5` → `day_5_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `flight_return` → `airport_transfer_out`: at the airport 2–4 h before departure (TRANSFER_BEFORE_DEPARTURE)
- `day_5_plan` → `airport_transfer_out`: last day ends before the airport pickup (DAY_ENDS_BEFORE_PICKUP)
- `flight_outbound` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `flight_return` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `hotel_stay` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `airport_transfer_in` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `airport_transfer_out` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_1_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_2_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_3_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_4_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_5_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)

### Disruptions to label

- `santorini_solo_luxury__outbound_delay_15`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=15)
- `santorini_solo_luxury__outbound_delay_90`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=90)
- `santorini_solo_luxury__outbound_delay_180`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=180)
- `santorini_solo_luxury__outbound_delay_300`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=300)
- `santorini_solo_luxury__outbound_delay_480`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=480)
- `santorini_solo_luxury__outbound_delay_600`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=600)
- `santorini_solo_luxury__return_delay_60`: FLIGHT_DELAY on `flight_return` (delay_minutes=60)
- `santorini_solo_luxury__return_delay_240`: FLIGHT_DELAY on `flight_return` (delay_minutes=240)
- `santorini_solo_luxury__outbound_cancellation`: FLIGHT_CANCELLATION on `flight_outbound` (—)
- `santorini_solo_luxury__return_cancellation`: FLIGHT_CANCELLATION on `flight_return` (—)
- `santorini_solo_luxury__hotel_overbooked`: HOTEL_OVERBOOKED on `hotel_stay` (—)
- `santorini_solo_luxury__storm_mid_stay`: WEATHER_STORM on `weather_day_3` (condition=thunderstorm)
- `santorini_solo_luxury__storm_last_day`: WEATHER_STORM on `weather_day_5` (condition=thunderstorm)
- `santorini_solo_luxury__hotel_price_spike_10`: PRICE_SPIKE on `hotel_stay` (pct=10)
- `santorini_solo_luxury__hotel_price_spike_30`: PRICE_SPIKE on `hotel_stay` (pct=30)
- `santorini_solo_luxury__return_fare_spike_60`: PRICE_SPIKE on `flight_return` (pct=60)

## goa_couple_domestic

Mumbai → Goa, 2027-07-01 – 2027-07-04, 2 traveller(s), budget 90000 INR.

### Tasks (planned)

| Task | Plan | Other options the planner may choose |
|---|---|---|
| `flight_outbound` | SK846 BOM→GOI 08:00–14:00, 92398.32 INR | ZZ883 BOM→GOI 11:30–17:30, 103486.12 INR<br>VY120 BOM→GOI 06:00–14:00, 115497.9 INR |
| `flight_return` | SK720 GOI→BOM 16:00–22:00, 68443.2 INR | ZZ757 GOI→BOM 09:00–15:00, 75287.52 INR<br>VY794 GOI→BOM 19:30–02:30, 82131.84 INR |
| `weather_day_1` | 2027-07-01: rain | — |
| `weather_day_2` | 2027-07-02: cloudy | — |
| `weather_day_3` | 2027-07-03: light_rain | — |
| `hotel_stay` | Goa Vista Suites, 2027-07-01 → 2027-07-04, check-in until 23:59, 35219.73 INR | Blue Horizon Hotel, 2027-07-01 → 2027-07-04, check-in until 23:59, 34849.0 INR<br>Goa Old Town Residence, 2027-07-01 → 2027-07-04, check-in until 23:59, 34849.0 INR |
| `airport_transfer_in` | pickup 14:30, drop-off 14:40 (10 min) | pickup 14:15, drop-off 14:25 (10 min)<br>pickup 14:45, drop-off 14:55 (10 min)<br>pickup 15:00, drop-off 15:10 (10 min) |
| `day_2_plan` | 2027-07-02: 09:30 Long Beach (outdoor); 12:30 Lunch at Taverna Elena; 14:30 Stillwater Spa; 18:00 Sunset at Clifftop Chapel (outdoor); 20:00 Dinner at Blue Door Kitchen | re-fillable with other nearby places |
| `day_3_plan` | 2027-07-03: 09:30 Cedar Ridge (outdoor); 12:30 Lunch at Lemon Court; 14:30 Seafront Park (outdoor); 18:00 Sunset at Clifftop Chapel (outdoor); 20:00 Dinner at Taverna Elena | re-fillable with other nearby places |
| `day_1_plan` | 2027-07-01: 18:00 Sunset at Lighthouse Point (outdoor); 20:00 Dinner at The Fig Tree | re-fillable with other nearby places |
| `airport_transfer_out` | pickup 12:50, drop-off 13:00 (10 min) | pickup 13:20, drop-off 13:30 (10 min)<br>pickup 12:20, drop-off 12:30 (10 min)<br>pickup 11:50, drop-off 12:00 (10 min) |
| `budget_check` | total 261673.95 of 90000.0 INR | — |

### Dependencies (each must hold after a repair)

- `flight_outbound` → `hotel_stay`: arrival + 90 min ≤ check-in cutoff (FLIGHT_BEFORE_CHECKIN)
- `flight_outbound` → `airport_transfer_in`: pickup 15–90 min after arrival (TRANSFER_AFTER_FLIGHT)
- `hotel_stay` → `airport_transfer_in`: transfer is routed to the booked hotel (TRANSFER_SERVES_HOTEL)
- `hotel_stay` → `airport_transfer_out`: transfer is routed to the booked hotel (TRANSFER_SERVES_HOTEL)
- `airport_transfer_in` → `day_1_plan`: reach the hotel before day 1 starts (TRANSFER_BEFORE_DAY_START)
- `hotel_stay` → `day_1_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_1` → `day_1_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_2_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_2` → `day_2_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_3_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_3` → `day_3_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `flight_return` → `airport_transfer_out`: at the airport 2–4 h before departure (TRANSFER_BEFORE_DEPARTURE)
- `day_3_plan` → `airport_transfer_out`: last day ends before the airport pickup (DAY_ENDS_BEFORE_PICKUP)
- `flight_outbound` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `flight_return` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `hotel_stay` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `airport_transfer_in` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `airport_transfer_out` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_1_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_2_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_3_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)

### Disruptions to label

- `goa_couple_domestic__outbound_delay_15`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=15)
- `goa_couple_domestic__outbound_delay_90`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=90)
- `goa_couple_domestic__outbound_delay_180`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=180)
- `goa_couple_domestic__outbound_delay_300`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=300)
- `goa_couple_domestic__outbound_delay_480`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=480)
- `goa_couple_domestic__outbound_delay_600`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=600)
- `goa_couple_domestic__return_delay_60`: FLIGHT_DELAY on `flight_return` (delay_minutes=60)
- `goa_couple_domestic__return_delay_240`: FLIGHT_DELAY on `flight_return` (delay_minutes=240)
- `goa_couple_domestic__outbound_cancellation`: FLIGHT_CANCELLATION on `flight_outbound` (—)
- `goa_couple_domestic__return_cancellation`: FLIGHT_CANCELLATION on `flight_return` (—)
- `goa_couple_domestic__hotel_overbooked`: HOTEL_OVERBOOKED on `hotel_stay` (—)
- `goa_couple_domestic__storm_mid_stay`: WEATHER_STORM on `weather_day_2` (condition=thunderstorm)
- `goa_couple_domestic__storm_last_day`: WEATHER_STORM on `weather_day_3` (condition=thunderstorm)
- `goa_couple_domestic__hotel_price_spike_10`: PRICE_SPIKE on `hotel_stay` (pct=10)
- `goa_couple_domestic__hotel_price_spike_30`: PRICE_SPIKE on `hotel_stay` (pct=30)
- `goa_couple_domestic__return_fare_spike_60`: PRICE_SPIKE on `flight_return` (pct=60)

## tokyo_family_week

London → Tokyo, 2027-03-01 – 2027-03-08, 4 traveller(s), budget 14000 GBP.

### Tasks (planned)

| Task | Plan | Other options the planner may choose |
|---|---|---|
| `flight_outbound` | SK201 LHR→HND 08:00–14:00, 969.3 GBP | ZZ238 LHR→HND 11:30–17:30, 1085.62 GBP<br>VY275 LHR→HND 06:00–14:00, 1211.63 GBP |
| `flight_return` | ZZ419 HND→LHR 16:00–22:00, 1144.44 GBP | VY456 HND→LHR 09:00–15:00, 1258.89 GBP<br>SK493 HND→LHR 19:30–02:30, 1373.33 GBP |
| `weather_day_1` | 2027-03-01: cloudy | — |
| `weather_day_2` | 2027-03-02: light_rain | — |
| `weather_day_3` | 2027-03-03: light_rain | — |
| `weather_day_4` | 2027-03-04: rain | — |
| `weather_day_5` | 2027-03-05: partly_cloudy | — |
| `weather_day_6` | 2027-03-06: cloudy | — |
| `weather_day_7` | 2027-03-07: light_rain | — |
| `hotel_stay` | Blue Horizon Hotel, 2027-03-01 → 2027-03-08, check-in until 23:59, 1997.49 GBP | Tokyo Vista Suites, 2027-03-01 → 2027-03-08, check-in until 23:59, 2130.66 GBP<br>Tokyo Harbour Hotel, 2027-03-01 → 2027-03-08, check-in until 23:59, 2241.63 GBP |
| `airport_transfer_in` | pickup 14:30, drop-off 15:10 (40 min) | pickup 14:15, drop-off 14:55 (40 min)<br>pickup 14:45, drop-off 15:25 (40 min)<br>pickup 15:00, drop-off 15:40 (40 min) |
| `day_2_plan` | 2027-03-02: 09:30 Old Bazaar; 12:30 Lunch at The Fig Tree; 14:30 Kiosk by the Square; 18:00 Sunset at Harbour Promenade (outdoor); 20:00 Dinner at Taverna Elena | re-fillable with other nearby places |
| `day_3_plan` | 2027-03-03: 09:30 Clifftop Chapel (outdoor); 12:30 Lunch at Blue Door Kitchen; 14:30 Contemporary Art Gallery; 18:00 Sunset at Lighthouse Point (outdoor); 20:00 Dinner at Taverna Elena | re-fillable with other nearby places |
| `day_4_plan` | 2027-03-04: 09:30 Old Bazaar; 12:30 Lunch at Harbour Grill; 14:30 Kiosk by the Square; 18:00 Sunset at Lighthouse Point (outdoor); 20:00 Dinner at Taverna Elena | re-fillable with other nearby places |
| `day_5_plan` | 2027-03-05: 09:30 Lighthouse Point (outdoor); 12:30 Lunch at Lemon Court; 14:30 Tokyo Archaeological Museum; 18:00 Sunset at Tokyo Old Town (outdoor); 20:00 Dinner at Blue Door Kitchen | re-fillable with other nearby places |
| `day_6_plan` | 2027-03-06: 09:30 Design District; 12:30 Lunch at The Fig Tree; 14:30 Morning Light Café; 18:00 Sunset at Lighthouse Point (outdoor); 20:00 Dinner at Blue Door Kitchen | re-fillable with other nearby places |
| `day_7_plan` | 2027-03-07: 09:30 Harbour Promenade (outdoor); 12:30 Lunch at Harbour Grill; 14:30 Contemporary Art Gallery; 18:00 Sunset at Lighthouse Point (outdoor); 20:00 Dinner at Blue Door Kitchen | re-fillable with other nearby places |
| `day_1_plan` | 2027-03-01: 18:00 Sunset at Tokyo Old Town (outdoor); 20:00 Dinner at Olive & Vine | re-fillable with other nearby places |
| `airport_transfer_out` | pickup 12:20, drop-off 13:00 (40 min) | pickup 12:50, drop-off 13:30 (40 min)<br>pickup 11:50, drop-off 12:30 (40 min)<br>pickup 11:20, drop-off 12:00 (40 min) |
| `budget_check` | total 6323.59 of 14000.0 GBP | — |

### Dependencies (each must hold after a repair)

- `flight_outbound` → `hotel_stay`: arrival + 90 min ≤ check-in cutoff (FLIGHT_BEFORE_CHECKIN)
- `flight_outbound` → `airport_transfer_in`: pickup 15–90 min after arrival (TRANSFER_AFTER_FLIGHT)
- `hotel_stay` → `airport_transfer_in`: transfer is routed to the booked hotel (TRANSFER_SERVES_HOTEL)
- `hotel_stay` → `airport_transfer_out`: transfer is routed to the booked hotel (TRANSFER_SERVES_HOTEL)
- `airport_transfer_in` → `day_1_plan`: reach the hotel before day 1 starts (TRANSFER_BEFORE_DAY_START)
- `hotel_stay` → `day_1_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_1` → `day_1_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_2_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_2` → `day_2_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_3_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_3` → `day_3_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_4_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_4` → `day_4_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_5_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_5` → `day_5_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_6_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_6` → `day_6_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `hotel_stay` → `day_7_plan`: day plan is based at the booked hotel (STAY_COVERS_DAY)
- `weather_day_7` → `day_7_plan`: no outdoor items in storms (WEATHER_SUITS_PLAN)
- `flight_return` → `airport_transfer_out`: at the airport 2–4 h before departure (TRANSFER_BEFORE_DEPARTURE)
- `day_7_plan` → `airport_transfer_out`: last day ends before the airport pickup (DAY_ENDS_BEFORE_PICKUP)
- `flight_outbound` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `flight_return` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `hotel_stay` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `airport_transfer_in` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `airport_transfer_out` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_1_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_2_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_3_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_4_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_5_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_6_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)
- `day_7_plan` → `budget_check`: budget uses this item's current cost (BUDGET_ITEM_CURRENT)

### Disruptions to label

- `tokyo_family_week__outbound_delay_15`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=15)
- `tokyo_family_week__outbound_delay_90`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=90)
- `tokyo_family_week__outbound_delay_180`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=180)
- `tokyo_family_week__outbound_delay_300`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=300)
- `tokyo_family_week__outbound_delay_480`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=480)
- `tokyo_family_week__outbound_delay_600`: FLIGHT_DELAY on `flight_outbound` (delay_minutes=600)
- `tokyo_family_week__return_delay_60`: FLIGHT_DELAY on `flight_return` (delay_minutes=60)
- `tokyo_family_week__return_delay_240`: FLIGHT_DELAY on `flight_return` (delay_minutes=240)
- `tokyo_family_week__outbound_cancellation`: FLIGHT_CANCELLATION on `flight_outbound` (—)
- `tokyo_family_week__return_cancellation`: FLIGHT_CANCELLATION on `flight_return` (—)
- `tokyo_family_week__hotel_overbooked`: HOTEL_OVERBOOKED on `hotel_stay` (—)
- `tokyo_family_week__storm_mid_stay`: WEATHER_STORM on `weather_day_4` (condition=thunderstorm)
- `tokyo_family_week__storm_last_day`: WEATHER_STORM on `weather_day_7` (condition=thunderstorm)
- `tokyo_family_week__hotel_price_spike_10`: PRICE_SPIKE on `hotel_stay` (pct=10)
- `tokyo_family_week__hotel_price_spike_30`: PRICE_SPIKE on `hotel_stay` (pct=30)
- `tokyo_family_week__return_fare_spike_60`: PRICE_SPIKE on `flight_return` (pct=60)

