"""
A little personality for utrains - because terminals don't have to be boring.

Just word lists and a picker. Everything here is cosmetic: the agent's actual
behaviour never depends on which quip got chosen. Set UTRAINS_SERIOUS=1 in the
environment to mute the jokes (pick() then returns the first, plain item).
"""

import os
import random

# Rotating "thinking" labels shown next to the spinner.
THINKING = [
    "Thinking", "Cooking", "Summoning bytes", "Consulting the docs",
    "Poking the terminal", "Doing nerd stuff", "Crunching numbers",
    "Hacking (the legal kind)", "Asking the rubber duck", "Reticulating splines",
    "Bribing the CPU", "Reading the manual (rare)",
]

# One-liners shown when a chat session starts.
GREETINGS = [
    "Utrains student detected - superpowers loading… ⚡",
    "Your wish is my command line. 🪄",
    "Let's make the terminal do your homework. 😎",
    "Type a goal, I'll do the typing. ⌨️",
    "Ready to break things… safely. 🧪",
]

# Tiny celebratory tags appended after a successful answer (sometimes).
WINS = [
    "Nailed it. 🎯", "Boom. 💥", "Too easy. 😏", "Ship it. 🚀",
    "Chef's kiss. 👌", "Another one bites the dust. 🎸", "GG. 🎮",
]

# Cheers when a student fixes their own broken command and it finally passes.
PRAISE = [
    "🎉 You found it! That's exactly how debugging feels.",
    "🙌 Nailed it - you fixed it yourself!",
    "✨ Green light! Your eyes are getting sharp.",
    "🥳 That's the one! Real DevOps muscle right there.",
    "👏 Boom - fixed and passing. Well spotted!",
    "🚀 You squashed it. On to the next one!",
]

# Sign-offs when you leave.
GOODBYES = [
    "Bye! Go build something cool. 👋",
    "Later, legend. 🫡",
    "Logging off. Stay curious! ✨",
    "Bye! May your builds be green. ✅",
]

# Rotating one-line tips shown at startup.
TIPS = [
    "Tip: type /help to see everything I can do.",
    "Tip: I use YOUR aws / kube / gh logins - no keys needed.",
    "Tip: nervous about a command? Just hit 2 to skip it.",
    "Tip: /model swaps my brain mid-chat.",
    'Tip: /memory add "..." makes me remember things.',
    "Tip: I run real commands - read before you hit Enter. 👀",
]

# Phrases that mean a task did NOT succeed - used to skip the win tag.
_FAIL_HINTS = ("could not", "couldn't", "error", "failed", "stopped after",
               "declined", "isn't", "not reachable")


def _muted() -> bool:
    return bool(os.environ.get("UTRAINS_SERIOUS"))


def pick(seq: list[str]) -> str:
    """A random item from the list (or the first item when jokes are muted)."""
    if _muted():
        return seq[0]
    return random.choice(seq)


def win_tag(answer: str) -> str:
    """A celebratory tag for a *successful* answer - '' otherwise or when muted."""
    if _muted():
        return ""
    low = answer.lower()
    if any(hint in low for hint in _FAIL_HINTS):
        return ""
    # Keep it occasional so it stays charming, not spammy (~40% of wins).
    return pick(WINS) if random.random() < 0.4 else ""
