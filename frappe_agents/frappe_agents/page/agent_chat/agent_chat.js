// Page shell only. The chat itself is frappe_agents.ChatUI from the desk bundle,
// which the form panel mounts too. Page scripts run after the boot bundles, so
// the global is always there.
frappe.provide("frappe_agents");

frappe.pages["agent-chat"].on_page_load = function (wrapper) {
	frappe_agents.chat_page = new frappe_agents.AgentChatPage(wrapper);
};

frappe.pages["agent-chat"].on_page_show = function () {
	if (frappe_agents.chat_page) {
		frappe_agents.chat_page.refresh();
	}
};

frappe_agents.AgentChatPage = class AgentChatPage {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("Agent Chat"),
			single_column: true,
		});
		this.agents = [];
		this.make();
		this.refresh();
	}

	make() {
		this.agent_field = this.page.add_field({
			fieldtype: "Select",
			fieldname: "agent",
			label: __("Agent"),
			options: [],
			change: () => this.on_agent_change(),
		});

		this.page.set_secondary_action(__("New Chat"), () => this.new_chat());

		this.chat = new frappe_agents.ChatUI({
			parent: this.page.main,
			on_conversation: (conversation, data) => this.on_conversation(conversation, data),
		});
	}

	refresh() {
		this.load_agents()
			.then(() => this.rehydrate())
			.catch((error) => console.error("frappe_agents: could not load agents", error));
	}

	load_agents() {
		return frappe.xcall("frappe_agents.api.list_agents").then((agents) => {
			this.agents = agents || [];
			this.agent_field.df.options = this.agents.map((agent) => ({
				value: agent.name,
				label: agent.agent_name || agent.name,
			}));
			if (this.agent_field.set_options) {
				this.agent_field.set_options();
			} else {
				this.agent_field.refresh();
			}

			if (!this.agents.length) {
				this.chat.set_agent(null);
				this.chat.show_empty(__("No agents are available to you."));
				return;
			}

			const current = this.agents.find((agent) => agent.name === this.chat.agent);
			const chosen = current ? current.name : this.agents[0].name;
			this.agent_field.set_value(chosen);
			this.chat.set_agent(chosen);
		});
	}

	on_agent_change() {
		const chosen = this.agent_field.get_value() || null;
		// An empty value while the option list is being rebuilt is not a choice.
		if (!chosen && this.agents.length) return;
		this.chat.set_agent(chosen);
	}

	/** Reopen the conversation named in the route, so a reload or a deep link resumes it. */
	rehydrate() {
		const conversation = this.take_route_conversation();
		if (!conversation || conversation === this.chat.conversation) return;
		this.chat.load_conversation(conversation);
	}

	/** route_options is a one-shot channel, so reading it consumes it. */
	take_route_conversation() {
		const from_route = this.route_conversation();
		if (from_route) return from_route;
		if (frappe.route_options && frappe.route_options.conversation) {
			const conversation = frappe.route_options.conversation;
			delete frappe.route_options.conversation;
			return conversation;
		}
		return null;
	}

	route_conversation() {
		const route = frappe.get_route() || [];
		return route[0] === "agent-chat" && route[1] ? decodeURIComponent(route[1]) : null;
	}

	on_conversation(conversation, data) {
		if (data) {
			// Rehydrated: the conversation names its agent, so follow it.
			if (data.agent && this.agents.some((agent) => agent.name === data.agent)) {
				this.agent_field.set_value(data.agent);
			}
			return;
		}
		// Freshly created by the first message: make it linkable.
		if (conversation && conversation !== this.route_conversation()) {
			frappe.set_route("agent-chat", conversation);
		}
	}

	new_chat() {
		this.chat.reset();
		this.chat.focus();
		if (this.route_conversation()) {
			frappe.set_route("agent-chat");
		}
	}
};
