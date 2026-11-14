# Docs

- `训练excel Training excel.xlsx`: FM training spreadsheet by harvestgreen22 on [FM-Arena](https://fm-arena.com/). Kept here as a reference for future development; all credit to its author.
- `ca_costs_fm27.csv`: CA cost of each attribute by position.
- `training_focuses_fm27.csv`: the attributes each training session works on.
- `attribute_impact_test.csv`: harvestgreen22's FM27 attribute test, as tabled by squirrel_plays in FM-Arena's
  [Player Attributes](https://fm-arena.com/threads/player-attributes.59/) thread (November 2026). Each row is the change in
  a team's win rate when every player's attribute is set to 1–18 and the rest stay at 12 (0% at 12). Tested at 1, 6, 12
  and 18, linear in between. It's harvestgreen22's own data, first posted on
  [sortitoutsi](https://sortitoutsi.net/content/78219/fm27-attribute-test-tactical-understanding-training-system-preset-tactics-ai-reverse-engineering-engine-for-test)
  (4 November 2026). Used by `weights/default.toml`.
- `attribute_importance_by_position.csv`: Purity's FM27 test in FM-Arena's
  [Attribute importance by position](https://fm-arena.com/threads/attribute-importance-by-position-in-fm27-a-test-on-my-own-tactic.125/)
  thread: goal difference per season for +1 in one player's attribute, by position (ST, AM, WB, CB, DM) and for the whole
  team; `*` = confirmed by the author. 200,000 matches with one 3-4-2-1 tactic. Kept as research reference.
- `screenshots/`: the README's screenshots, taken from a demo save (no real save's names).
