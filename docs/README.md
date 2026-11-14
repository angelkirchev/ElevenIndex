# Reference data

What the weights are built from, and other research worth keeping:

- `attribute_impact_test.csv`: harvestgreen22's FM27 attribute test (first posted on
  [sortitoutsi](https://sortitoutsi.net/content/78219/fm27-attribute-test-tactical-understanding-training-system-preset-tactics-ai-reverse-engineering-engine-for-test),
  4 November 2026), as tabled by squirrel_plays in FM-Arena's [Player Attributes](https://fm-arena.com/threads/player-attributes.59/)
  thread. Each row: how a team's win rate changes when every player's value of one attribute is set to 1, 6, 12 or 18
  (the rest at 12; 0% at 12), with straight lines between. The outfield weights in `weights/default.toml` use the gain
  from 9 to 15.
- `ca_costs_fm27.csv`: FM27's current-ability cost of each attribute at each position (a row per attribute, a column per
  position; blank = it costs nothing there). The weights multiply the test's gains by these, so an attribute FM doesn't
  count at a position gets no weight there.
- `attribute_importance_by_position.csv`: Purity's FM27 test from FM-Arena's
  [Attribute importance by position](https://fm-arena.com/threads/attribute-importance-by-position-in-fm27-a-test-on-my-own-tactic.125/)
  thread: goal difference per season from +1 in one attribute of one player, by position (ST, AM, WB, CB, DM) and for
  the whole team (`*` = confirmed by its author). One 3-4-2-1 tactic over 200,000 matches. Kept for comparison.
- `训练excel Training excel.xlsx`: harvestgreen22's FM training spreadsheet from [FM-Arena](https://fm-arena.com/), kept for
  reference; all credit to its author.
- `screenshots/`: the README's pictures, from a demo save.
- `logo-icon.svg`, `logo.png`, `FMAnalyst.ico`: the logo (the icon, the README's wordmark, the app's icon).
