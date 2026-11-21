# Reference data

What the weights are built from, and other research worth keeping:

- `attribute_test_fm27_2.csv`: harvestgreen22's FM27 attribute test, final report (FM-Arena,
  [FM27 New Player Attribute (1,4,8,12,16,20) Test](https://fm-arena.com/threads/incomplete-in-progress-fm27-new-player-attribute-1-4-8-12-16-20-test-updated-21-11-2026.276/),
  updated 21 November 2026), run with lqf0624's fm-tactics-lab: a team with every attribute at 12 against the 19
  Premier League sides' own tactics, 181,830 matches. Each row: the win rate (percentage points) with one attribute at
  1, 4, 8, 12, 16 and 20 against 12, and the whole swing from 1 to 20; for home and away matches equally weighted, home
  alone and away alone. The outfield weights in `weights/default.toml` come from it: an attribute counts when home and
  away together gain at least the test's error from 1 to 20 (2.5 points), weighted by the home gain (his advice: the
  more reliable ranking), scaled so Pace is 20. His notes: Pace and Acceleration matter at every position and in every
  tactic; Stamina, Jumping Reach and Anticipation next; Work Rate and Concentration hurt when low; a weaker foot only
  costs at 1 (all its gain is from 1 to 12, +3.9 points in all).
- `attribute_impact_test.csv`: harvestgreen22's first FM27 attribute test (first posted on
  [sortitoutsi](https://sortitoutsi.net/content/78219/fm27-attribute-test-tactical-understanding-training-system-preset-tactics-ai-reverse-engineering-engine-for-test#comment_910309),
  4 November 2026), as tabled by squirrel_plays in FM-Arena's [Player Attributes](https://fm-arena.com/threads/player-attributes.59/)
  thread. Each row: how a team's win rate changes when every player's value of one attribute is set to 1, 6, 12 or 18
  (the rest at 12; 0% at 12), with straight lines between. The outfield attribute weights (1-20) in `weights/default.toml`
  (one column for every position) were each attribute's whole swing from 1 to 18, scaled so Pace (the biggest) is 20,
  until the second test replaced them (it ranks them much the same); the goalkeeping attributes, which neither test
  covers, are 15.
- `ca_costs_fm27.csv`: FM's current-ability cost of each attribute at each position, as FM Scout tables it (unchanged
  for many FM versions; a row per attribute, a column per position; 1 = the smallest cost, blank = it costs nothing
  there). An attribute's weight at a position = its CA cost there x its attribute
  weight, so an attribute FM doesn't count at a position gets no weight there.
- `attribute_importance_by_position.csv`: Purity's FM27 test from FM-Arena's
  [Attribute importance by position](https://fm-arena.com/threads/attribute-importance-by-position-in-fm27-a-test-on-my-own-tactic.125/)
  thread: goal difference per season from +1 in one attribute of one player, by position (ST, AM, WB, CB, DM) and for
  the whole team (`*` = confirmed by its author). One 3-4-2-1 tactic over 200,000 matches. Kept for comparison.
- `训练excel Training excel (20261115 Ver5).xlsx` and `training_schedule_test.csv`: harvestgreen22's FM27 training test
  (a season's CA gain under each team schedule), behind Squad › Training's schedules; all credit to its author.
- `screenshots/`: the README's pictures, from a demo save.
- `logo-icon.svg`, `logo.png`, `ElevenIndex.ico`: the logo (the icon, the README's wordmark, the app's icon).
