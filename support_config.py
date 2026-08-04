"""
Where the in-app Support page sends people.

READ THIS BEFORE FILLING IT IN
------------------------------
Mixed Games Tracker runs entirely on each player's own machine - there is no
server in the middle. That means the app must never handle card details or
hold a payment provider's secret key: anything shipped here is readable by
everyone you give a copy to.

So these are just plain, public links out to a checkout page that somebody
else hosts and secures. Create them in your provider's dashboard and paste
the resulting URLs in below. Any of these work:

  * Stripe Payment Links  - one-off and recurring, dashboard.stripe.com
  * Ko-fi                 - one-off and monthly memberships
  * GitHub Sponsors       - monthly/yearly tiers
  * PayPal.me             - one-off only
  * Buy Me a Coffee       - one-off and monthly

Leave an entry as "" and that option is simply not offered. Leave them all
empty - the default - and the Support page and its nav link disappear
entirely, so an unconfigured copy of the app never asks anyone for money.

Paying is always optional. Nothing in the app is gated behind it, and the
Support page should never claim otherwise.
"""

SUPPORT_LINKS = {
    # One-off "buy me a coffee" style donation.
    "one_off": "",
    # Recurring. Create one payment link per interval in your provider's
    # dashboard and paste each URL here.
    "monthly": "",
    "quarterly": "",
    "yearly": "",
}

# Optional: shown on the Support page so people can reach you. An email
# address, a Discord invite, a GitHub URL - or "" to show nothing.
SUPPORT_CONTACT = ""

# Shown at the top of the Support page. Keep it honest - the app is free and
# stays free whether or not anyone pays.
SUPPORT_BLURB = (
    "Mixed Games Tracker is free, and every feature stays free. "
    "If it's saved you time or found you a leak, you can chip in below - "
    "but there's no obligation, and nothing is locked behind it."
)


def configured_options():
    """The payment options that actually have a link set, in display order."""
    labels = [
        ("one_off", "One-off", "A single thank-you, any time"),
        ("monthly", "Monthly", "Ongoing support, cancel whenever"),
        ("quarterly", "Quarterly", "Every three months"),
        ("yearly", "Yearly", "Once a year"),
    ]
    return [
        {"key": key, "label": label, "blurb": blurb, "url": SUPPORT_LINKS[key]}
        for key, label, blurb in labels
        if SUPPORT_LINKS.get(key)
    ]


def is_enabled():
    """No links set up means no Support page and no nav link at all."""
    return bool(configured_options())
