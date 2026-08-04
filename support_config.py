"""Whether the site-wide Ko-fi "Support me" button is shown.

Mixed Games Tracker runs entirely on each player's own machine - there is no
server in the middle. That means the app must never handle card details or
hold a payment provider's secret key: anything shipped here is readable by
everyone you give a copy to. Ko-fi's floating widget sidesteps that
entirely - it's just a script that draws a button linking out to your own
Ko-fi page, which is what actually takes the payment.

Set KOFI_USERNAME to the part after ko-fi.com/ in your page's URL and the
button appears at the bottom-left of every page. Leave it as "" - the
default - and nothing is shown and no request to ko-fi.com is ever made, so
an unconfigured copy of the app never asks anyone for money.
"""

KOFI_USERNAME = "clynchh"


def is_enabled():
    return bool(KOFI_USERNAME)
