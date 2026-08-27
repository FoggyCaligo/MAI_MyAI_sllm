"""Memory turn lifecycle.

Primary boundary:
- begin_turn: store utterance, update activation, build automatic recall context.
- finish_turn: extract durable memory and commit graph mutations.
"""
