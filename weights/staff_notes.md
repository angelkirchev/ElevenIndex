# Staff weights: how they're set

ElevenIndex scores a staff member for every job the same way it scores a player for every position:

**job score = Σ(attribute value × weight) ÷ Σ(weights)**, on FM's 1–20 scale, and only when every weighted attribute is known.

## FM's words as numbers

FM shows staff attributes as words, each standing for a range of the 1–20 scale. A word counts as the middle of its range:

| Word | Range | Counts as |
|---|---|---|
| Elite | 20 | 20 |
| Outstanding | 18–19 | 18.5 |
| Very Good | 15–17 | 16 |
| Good | 12–14 | 13 |
| Average | 10–11 | 10.5 |
| Competent | 7–9 | 8 |
| Reasonable | 4–6 | 5 |
| Unsuited | 1–3 | 2 |

## The rule behind the weights

Each job's weights start from FM27's **key attributes** for it: the ones a staff member's profile shows in green for
that job.

- **4**: the job's own skill: Physiotherapy for a physio, Fitness for a fitness coach, the two coaching attributes of a
  coaching group (Attacking + Tactical for attacking tactical…), and judging ability and potential for scouts, the
  director of football and the head of youth development.
- **2**: FM's other key attributes for the job, such as Authority, Determination and Motivating for coaches.
- **1**: attributes that help without being key, such as Working With Youngsters for coaches.

Negotiating and Adaptability, key for a director of football and a scout, aren't on the `Search - Staff View`, so
they can't be weighted. Hidden attributes such as Professionalism aren't shown either: check those by hand when it
matters (for a head of youth development, for one).

## Changing them

The weights are in `weights/default.toml` (`[staff.<job>]`). Any job can be changed there; a new job needs its own
`[staff.<label>]` table and an entry in `[staff_names]`.
