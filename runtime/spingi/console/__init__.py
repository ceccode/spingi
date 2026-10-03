"""Operator console v0 (ADR-0008): live event lines on the terminal and retry / skip / abort prompts.

Ctrl+C is the stop button (handled in spingi.session): it e-stops the robot and ends the run.
"""

from spingi.console.terminal import ConsoleHuman, ConsoleReporter

__all__ = ["ConsoleHuman", "ConsoleReporter"]
