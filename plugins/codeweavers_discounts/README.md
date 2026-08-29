# CodeWeavers discount checker

Checks CrossOver + and CrossOver Life at <https://www.codeweavers.com/store>
against the normal prices stored in `config.toml`. It prints one line per
discounted plan and prints nothing when all prices are normal and no promotion
language is present. It also checks visible page text for the words `sale`,
`special`, `offer`, and `discount`.
The standard “Special Renewal Pricing” product benefit is excluded from keyword
detection.

The plugin exits with an error if the page cannot be fetched, its pricing markup
can no longer be parsed, or a configured plan is missing. Update
`[normal_prices]` in `config.toml` when CodeWeavers permanently changes its
standard pricing.

It also checks <https://www.codeweavers.com/store/promotions>. The normal “Sorry,
there are no promotions currently active” message produces no output. If that
message is absent, the plugin prints the promotions page URL.
