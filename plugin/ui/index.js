function opencodeTab() {
    return {
        sessions: [],
        statuses: {},
        currentId: null,
        messages: [],
        draft: '',
        connected: null, // null = unknown, false = offline
        isBusy: false,
        _pollTimer: null,
        _msgCount: 0,

        async init() {
            await this.refresh();
            this._pollTimer = setInterval(() => this.poll(), 2000);
            AM.onCleanup(() => this.stop());
        },

        stop() {
            if (this._pollTimer) clearInterval(this._pollTimer);
            this._pollTimer = null;
        },

        async refresh() {
            await Promise.all([this.loadStatus(), this.loadSessions()]);
        },

        async loadStatus() {
            try {
                const resp = await AM.fetch('/plugins/opencode/status');
                if (resp && resp.ok) {
                    this.connected = (await resp.json()).connected;
                } else {
                    this.connected = false;
                }
            } catch (e) {
                this.connected = false;
            }
        },

        async loadSessions() {
            try {
                const resp = await AM.fetch('/plugins/opencode/sessions');
                if (!resp || !resp.ok) return;
                this.sessions = await resp.json();
                if (!this.currentId && this.sessions.length) {
                    this.openSession(this.sessions[0].id);
                }
            } catch (e) { /* offline handled by status */ }
        },

        async openSession(id) {
            this.currentId = id;
            this.messages = [];
            this._msgCount = 0;
            await this.loadMessages();
            this.scrollToBottom();
        },

        async loadMessages() {
            if (!this.currentId) return;
            try {
                const resp = await AM.fetch(`/plugins/opencode/sessions/${this.currentId}/messages`);
                if (!resp || !resp.ok) return;
                this.messages = await resp.json();
                if (this.messages.length !== this._msgCount) {
                    this._msgCount = this.messages.length;
                    this.scrollToBottom();
                }
            } catch (e) { /* ignore poll errors */ }
        },

        async poll() {
            if (this.connected === false) return;
            try {
                const resp = await AM.fetch('/plugins/opencode/statuses');
                if (!resp || !resp.ok) return;
                this.statuses = await resp.json();
            } catch (e) { return; }
            const st = this.statuses[this.currentId];
            this.isBusy = !!(st && st.event && st.event !== 'idle');
            // Always refresh while a session is open — the agent can finish
            // between polls, and the busy flag alone would miss that.
            if (this.currentId) {
                await this.loadMessages();
            }
        },

        async newSession() {
            const title = (prompt('Session title (optional):') || '').trim();
            try {
                const resp = await AM.fetch('/plugins/opencode/sessions', {
                    method: 'POST',
                    body: { title: title || null }
                });
                if (!resp || !resp.ok) {
                    AM.toast('Failed to create session', 'error');
                    return;
                }
                const session = await resp.json();
                await this.loadSessions();
                this.openSession(session.id);
                AM.toast('Session created', 'success');
            } catch (e) {
                AM.toast('Failed to create session', 'error');
            }
        },

        async send() {
            const text = this.draft.trim();
            if (!text || !this.currentId) return;
            this.draft = '';
            this.autosize();
            this.isBusy = true;
            // Optimistic user message.
            this.messages.push({
                info: { id: 'local-' + Date.now(), role: 'user' },
                parts: [{ type: 'text', text }]
            });
            this._msgCount = this.messages.length;
            this.scrollToBottom();
            try {
                const resp = await AM.fetch(`/plugins/opencode/sessions/${this.currentId}/prompt`, {
                    method: 'POST',
                    body: { text }
                });
                if (!resp || !resp.ok) {
                    const detail = resp ? await resp.json().catch(() => ({})) : {};
                    AM.toast(detail.detail || 'Failed to send prompt', 'error');
                    this.isBusy = false;
                    return;
                }
                await this.loadMessages();
            } catch (e) {
                AM.toast('Failed to send prompt', 'error');
                this.isBusy = false;
            }
        },

        async abort() {
            if (!this.currentId) return;
            try {
                await AM.fetch(`/plugins/opencode/sessions/${this.currentId}/abort`, { method: 'POST' });
                this.isBusy = false;
                await this.loadMessages();
            } catch (e) {
                AM.toast('Failed to abort', 'error');
            }
        },

        // --- rendering helpers ---

        renderableParts(m) {
            return (m.parts || []).filter(p =>
                p.type === 'text' || p.type === 'tool' || p.type === 'file');
        },

        md(text) {
            return AM.utils.renderMarkdown(text || '');
        },

        toolName(p) {
            const st = p.state || {};
            return st.title || p.tool || 'tool';
        },

        toolDetail(p) {
            const st = p.state || {};
            let out = '';
            if (st.input) out += 'input:\n' + (typeof st.input === 'string' ? st.input : JSON.stringify(st.input, null, 2));
            if (st.output) out += (out ? '\n\n' : '') + 'output:\n' + (typeof st.output === 'string' ? st.output : JSON.stringify(st.output, null, 2));
            return out || '(no detail)';
        },

        fileLabel(p) {
            const f = p.file || {};
            return (f.status || 'changed') + ' ' + (f.path || '');
        },

        autosize() {
            if (this.$refs.draft) AM.utils.autoResize(this.$refs.draft);
        },

        scrollToBottom() {
            requestAnimationFrame(() => {
                const el = this.$refs.messages;
                if (el) el.scrollTop = el.scrollHeight;
            });
        },
    };
}
