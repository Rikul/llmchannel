// ── IRC Chat – Main Application ─────────────────────────────────────

var ChatApp = (function () {
    var state = {
        messages: [],
        streamingMessages: {},  // msg_id -> true (DOM elements found by id)
        pendingStreams: {},
        evtSource: null,
    };

    // ── Helpers ──────────────────────────────────────────────────────

    function formatTime(ts) {
        var d = new Date(ts * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(text));
        return div.innerHTML;
    }

    function endpointHost(url) {
        var match = /^https?:\/\/([^\/]+)/.exec(url || '');
        return match ? match[1] : 'no endpoint';
    }

    function getInitial(name) {
        return (name || '?').charAt(0).toUpperCase();
    }

    function isNearBottom() {
        var el = document.getElementById('messages');
        return el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    }

    function scrollToBottom(force) {
        var el = document.getElementById('messages');
        if (force || isNearBottom()) {
            el.scrollTop = el.scrollHeight;
        }
    }

    // ── Message Rendering ───────────────────────────────────────────

    function createMessageHtml(msg) {
        var typeClass = msg.type === 'operator' ? 'msg-operator' : '';
        if (msg.type === 'system') typeClass = 'msg-system';

        if (msg.type === 'system') {
            return '<div class="msg-system message-enter" id="msg-' + msg.id + '">' +
                escapeHtml(msg.content) +
                '</div>';
        }

        var html = '<div class="message message-enter ' + typeClass + '" id="msg-' + msg.id + '">' +
            '<div class="avatar" style="background-color: ' + escapeHtml(msg.color) + ';">' +
            getInitial(msg.sender) +
            '</div>' +
            '<div style="flex: 1; min-width: 0;">' +
            '<div class="msg-meta">' +
            '<span class="msg-sender" style="color: ' + escapeHtml(msg.color) + ';">' +
            escapeHtml(msg.sender) +
            '</span>' +
            '<span class="msg-time">' + formatTime(msg.timestamp) + '</span>' +
            '</div>' +
            '<div class="message-content" id="content-' + msg.id + '">' +
            escapeHtml(msg.content) +
            '</div>' +
            '</div>' +
            '</div>';

        return html;
    }

    function appendMessage(msg) {
        var $container = $('#messages');
        $container.append(createMessageHtml(msg));
        scrollToBottom();
    }

    function renderAllMessages() {
        var $container = $('#messages');
        $container.empty();
        $.each(state.messages, function (i, msg) {
            $container.append(createMessageHtml(msg));
        });
        scrollToBottom(true);
    }

    // ── SSE Connection ──────────────────────────────────────────────

    function connectSSE() {
        if (state.evtSource) {
            state.evtSource.close();
        }

        var evtSource = new EventSource('/api/stream');
        state.evtSource = evtSource;

        evtSource.addEventListener('new_message', function (e) {
            var msg = JSON.parse(e.data);
            state.messages.push(msg);
            appendMessage(msg);
        });

        evtSource.addEventListener('stream_start', function (e) {
            var msg = JSON.parse(e.data);
            state.pendingStreams[msg.id] = msg;
        });

        evtSource.addEventListener('stream_token', function (e) {
            var data = JSON.parse(e.data);
            var $content = $('#content-' + data.id);
            if (!$content.length) {
                var pending = state.pendingStreams[data.id];
                if (pending) {
                    pending.content = data.token;
                    state.messages.push(pending);
                    appendMessage(pending);
                    $('#content-' + pending.id).addClass('streaming-cursor');
                    state.streamingMessages[pending.id] = true;
                    delete state.pendingStreams[data.id];
                    scrollToBottom();
                    return;
                }
            }
            if ($content.length) {
                // Append token as text (not html)
                var current = $content.text();
                $content.text(current + data.token);
                scrollToBottom();
            }
        });

        evtSource.addEventListener('stream_end', function (e) {
            var data = JSON.parse(e.data);
            $('#content-' + data.id).removeClass('streaming-cursor');
            delete state.streamingMessages[data.id];
            delete state.pendingStreams[data.id];

            // Update the stored message content
            for (var i = state.messages.length - 1; i >= 0; i--) {
                if (state.messages[i].id === data.id) {
                    state.messages[i].content = $('#content-' + data.id).text();
                    state.messages[i].streaming = false;
                    break;
                }
            }
        });

        evtSource.addEventListener('clear', function () {
            state.messages = [];
            state.streamingMessages = {};
            $('#messages').empty();
        });

        evtSource.onerror = function () {
            console.warn('SSE connection lost, reconnecting...');
        };
    }

    // ── Message Sending ─────────────────────────────────────────────

    function handleSend(e) {
        e.preventDefault();
        var $input = $('#chat-input');
        var content = $.trim($input.val());
        if (!content) return;

        $input.val('').focus();

        $.ajax({
            url: '/api/messages',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ content: content }),
        });
    }

    // ── Sidebar ─────────────────────────────────────────────────────

    function refreshSidebar() {
        $.getJSON('/api/bots', function (bots) {
            var $list = $('#user-list');
            $list.empty();

            // Operator always first
            $list.append(
                '<div class="user-item">' +
                '<span class="status-dot online"></span>' +
                '<div>' +
                '<div style="font-weight: 600; font-size: 0.8125rem; color: #3B82F6;">Operator</div>' +
                '<div style="font-size: 0.6875rem; color: var(--text-muted);">you</div>' +
                '</div>' +
                '</div>'
            );

            // Bots
            $.each(bots, function (i, bot) {
                var statusClass = bot.enabled ? 'online' : 'offline';
                var subtitle = bot.model + ' @ ' + endpointHost(bot.base_url);
                if (!bot.enabled) subtitle += ' (off)';

                $list.append(
                    '<div class="user-item">' +
                    '<span class="status-dot ' + statusClass + '"></span>' +
                    '<div>' +
                    '<div style="font-weight: 600; font-size: 0.8125rem; color: ' + escapeHtml(bot.color) + ';">' +
                    escapeHtml(bot.nickname) +
                    '</div>' +
                    '<div style="font-size: 0.6875rem; color: var(--text-muted);">' +
                    escapeHtml(subtitle) +
                    '</div>' +
                    '</div>' +
                    '</div>'
                );
            });

            // Update participant count
            var count = bots.length + 1; // +1 for operator
            $('#participant-count').text(count + ' participant' + (count !== 1 ? 's' : ''));
        });
    }

    // ── Init ────────────────────────────────────────────────────────

    function init() {
        // Load existing messages
        $.getJSON('/api/messages', function (msgs) {
            state.messages = msgs;
            renderAllMessages();
        });

        // Load sidebar
        refreshSidebar();

        // Connect SSE
        connectSSE();

        // Form handler
        $('#chat-form').on('submit', handleSend);

        // Also send on Enter in textarea (if we change to textarea later)
        $('#chat-input').on('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                $('#chat-form').trigger('submit');
            }
        });

        // Clear chat button
        $('#btn-clear').on('click', function () {
            if (confirm('Clear all messages?')) {
                $.post('/api/clear');
            }
        });
    }

    // ── Public API ──────────────────────────────────────────────────

    return {
        init: init,
        refreshSidebar: refreshSidebar,
        scrollToBottom: scrollToBottom,
    };
})();

$(function () {
    ChatApp.init();
});
