app_name = "frappe_agents"
app_title = "Agents"
app_publisher = "Malik AlZubaidi"
app_description = "Agent runtime for Frappe. Agents are records, not deployments."
app_email = "malik@leam.ae"
app_license = "mit"

# Desk
# ----

# The apps screen tile. Shown to anyone holding an agent role; everyone else
# would only land on a workspace they cannot read.
add_to_apps_screen = [
	{
		"name": "frappe_agents",
		"logo": "/assets/frappe_agents/images/logo.svg",
		"title": "Agents",
		"route": "/desk/frappe-agents",
		"has_permission": "frappe_agents.utils.has_app_permission",
	}
]

app_include_js = ["frappe_agents.bundle.js"]

# Puts the form-eligible agents on the boot payload so the Ask Agent button
# can decide per form without a server call.
extend_bootinfo = ["frappe_agents.boot.boot_form_agents"]

# Setup
# -----

after_install = "frappe_agents.install.after_install"

# Keep the Agent Tool registry in step with the code that ships the handlers.
after_migrate = "frappe_agents.tools.registry.sync_tools"

# Modules that expose agent tools. Each one defines a TOOLS list.
# Other apps extend this from their own hooks.py.
agent_tools = [
	"frappe_agents.tools.read_tools",
	"frappe_agents.tools.context_tools",
	"frappe_agents.tools.draft_tools",
	"frappe_agents.tools.extraction_tools",
	"frappe_agents.tools.builder_tools",
]

# Scheduled jobs
# --------------

# A proposal nobody decided is not a decision. Sweep pending actions past the
# expiry in Agent Settings so the agent has to make its case again.
scheduler_events = {
	"daily": [
		"frappe_agents.actions.expire_stale_actions",
	],
}
