# SPDX-License-Identifier: MIT
"""Wall-E server: the off-device half of the robot.

Layers, deliberately separate (see docs/05-architecture.md):

* :mod:`walle.protocol`  - the wire format, mirrored in the firmware.
* :mod:`walle.session`   - orchestration: one connected robot, one turn at a time.
* :mod:`walle.stt`, :mod:`walle.tts`, :mod:`walle.llm`, :mod:`walle.vision`
                         - swappable inference backends.
* :mod:`walle.intent`    - offline command routing, so the robot works without
                           the internet.
* :mod:`walle.smarthome` - allowlisted home control.
* :mod:`walle.memory`    - local SQLite: preferences and conversation history.
"""

__version__ = "1.0.0"
