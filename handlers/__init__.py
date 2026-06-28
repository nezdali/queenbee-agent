"""
Handlers package — re-exports the public surface used by bot.py.
"""

# Core chat
from .core import (
    start_command,
    help_command,
    clear_command,
    handle_message,
    handle_photo,
    model_command,
    setmodel_command,
)

# Group restriction
from .group import handle_my_chat_member

# Queen Bee tools
from .queen_bee import (
    _handle_qb_request,
    qb_callback,
    qbtest_command,
    qbsave_command,
    qbdiscard_command,
    qbfix_command,
    qbdebug_command,
    tools_command,
    runtool_command,
    deltool_command,
    edittool_command,
    edittool_callback,
    toolhelp_command,
    toolhelp_callback,
    newtool_command,
    tool_smart_callback,
)

# Admin user/role management
from .admin_users import (
    adduser_command,
    deluser_command,
    listusers_command,
    listroles_command,
    setrole_command,
)

# Strava (optional integration)
from .strava_handlers import (
    stravaconnect_command,
    stravaauth_command,
    stravahelp_command,
)

# Shared state
from .utils import conversation_manager
