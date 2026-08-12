app_name = "frappe_agents"
app_title = "Frappe Agents"
app_publisher = "Malik AlZubaidi"
app_description = "Agent runtime for Frappe. Agents are records, not deployments."
app_email = "malikzu.sg@gmail.com"
app_license = "mit"

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
]
