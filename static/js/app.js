// ── IRC Chat – Main Application ─────────────────────────────────────

var ChatApp = (function () {
    var state = {
        messages: [],
        streamingMessages: {},  // msg_id -> true (DOM elements found by id)
        pendingStreams: {},
        evtSource: null,
        liveConversation: null,  // the conversation that accepts messages
        viewing: null,           // archived conversation shown read-only, or null
    };

    // ── Helpers ──────────────────────────────────────────────────────

    function formatTime(ts) {
        var d = new Date(ts * 1000);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function formatDate(ts) {
        var d = new Date(ts * 1000);
        var sameDay = d.toDateString() === new Date().toDateString();
        return sameDay ?
            d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) :
            d.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function conversationTitle(conv) {
        if (!conv) return '#llm-channel';
        return conv.title || ('Untitled · ' + formatDate(conv.created_at));
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
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
            if (msg.streaming) {
                $('#content-' + msg.id).addClass('streaming-cursor');
                state.streamingMessages[msg.id] = true;
            }
        });
        if (!state.messages.length) {
            $container.append(
                '<div class="msg-system" style="padding: 24px 16px;">' +
                (state.viewing ? 'This conversation has no messages.' :
                    'New conversation. Say something to get the bots going.') +
                '</div>'
            );
        }
        scrollToBottom(true);
    }

    // ── Conversations ───────────────────────────────────────────────

    function loadLive() {
        state.streamingMessages = {};
        state.pendingStreams = {};
        $.getJSON('/api/messages', function (msgs) {
            state.messages = msgs;
            if (!state.viewing) renderAllMessages();
        });
    }

    function refreshConversations() {
        $.getJSON('/api/conversations', function (convs) {
            var $list = $('#conversation-list');
            $list.empty();
            $.each(convs, function (i, conv) {
                if (!conv.archived) {
                    state.liveConversation = conv;
                }
                var isActive = state.viewing ? state.viewing.id === conv.id : !conv.archived;
                var meta = conv.archived ?
                    formatDate(conv.archived_at) + ' · ' + conv.message_count + ' msgs' :
                    'live · ' + conv.message_count + ' msgs';
                var $item = $('<button class="conversation-item"></button>')
                    .toggleClass('active', isActive)
                    .attr('data-conversation-id', conv.id)
                    .append($('<div class="conversation-item-title"></div>')
                        .text(conv.archived ? conversationTitle(conv) : '● Live chat'))
                    .append($('<div class="conversation-item-meta"></div>').text(meta));
                $item.on('click', function () {
                    if (conv.archived) {
                        viewConversation(conv.id);
                    } else {
                        showLive();
                    }
                });
                $list.append($item);
            });
            updateHeader();
        });
    }

    function updateHeader() {
        var conv = state.viewing || state.liveConversation;
        $('#conversation-title').text(conversationTitle(conv));
    }

    function renderBotSnapshot(conv) {
        var $panel = $('#archive-bots').empty();
        var bots = conv.bots || [];

        if (!bots.length && !conv.system_prompt) {
            $panel.append($('<div class="snapshot-bot-meta"></div>').text(
                'No bot profiles were saved with this conversation ' +
                '(it was archived before profiles were recorded).'
            ));
            return;
        }

        if (conv.system_prompt) {
            var limit = (conv.bot_chain_limit === null || conv.bot_chain_limit === undefined) ?
                '' : ' · bot-to-bot reply limit ' + conv.bot_chain_limit;
            $panel.append(
                $('<details class="snapshot-prompt"></details>')
                    .append($('<summary></summary>').text('Main system prompt' + limit))
                    .append($('<pre></pre>').text(conv.system_prompt))
            );
        }

        var $grid = $('<div class="archive-bots-grid"></div>');
        $.each(bots, function (i, bot) {
            var settings = [];
            if (bot.temperature !== null && bot.temperature !== undefined) {
                settings.push('temperature ' + bot.temperature);
            }
            if (bot.max_tokens) settings.push('max tokens ' + bot.max_tokens);

            var $name = $('<div class="snapshot-bot-name"></div>')
                .css('color', bot.color)
                .text(bot.nickname);
            if (bot.participated) $name.append('<span class="snapshot-badge">spoke</span>');
            if (!bot.enabled) $name.append('<span class="snapshot-badge">off</span>');

            var $card = $('<div class="snapshot-bot"></div>')
                .append($name)
                .append($('<div class="snapshot-bot-meta"></div>')
                    .text(bot.model + ' @ ' + endpointHost(bot.base_url)));
            if (settings.length) {
                $card.append($('<div class="snapshot-bot-meta"></div>').text(settings.join(' · ')));
            }
            $card.append($('<div class="snapshot-persona"></div>')
                .text(bot.persona || '(no persona)'));
            $grid.append($card);
        });
        $panel.append($grid);
    }

    function viewConversation(conversationId) {
        $.getJSON('/api/conversations/' + conversationId, function (conv) {
            state.viewing = conv;
            state.messages = conv.messages || [];
            $('#archive-banner').css('display', 'flex');
            $('#archive-banner-title').text(conversationTitle(conv));
            renderBotSnapshot(conv);
            $('#input-area').hide();
            renderAllMessages();
            refreshConversations();
        });
    }

    function showLive() {
        state.viewing = null;
        $('#archive-banner').hide();
        $('#archive-bots').hide();
        $('#input-area').show();
        loadLive();
        refreshConversations();
        $('#chat-input').trigger('focus');
    }

    function startNewConversation() {
        if (!confirm('Archive this conversation and start a new one?')) return;
        $.ajax({
            url: '/api/conversations',
            method: 'POST',
            error: function () {
                alert('Could not start a new conversation.');
            },
        });
        // Every open tab switches when the server sends conversation_changed
    }

    function renameViewed() {
        if (!state.viewing) return;
        var title = prompt('Conversation name:', state.viewing.title || '');
        if (title === null) return;
        var id = state.viewing.id;
        $.ajax({
            url: '/api/conversations/' + id,
            method: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify({ title: title }),
            success: function (conv) {
                if (state.viewing && state.viewing.id === id) {
                    state.viewing.title = conv.title;
                    $('#archive-banner-title').text(conversationTitle(conv));
                }
                refreshConversations();
            },
        });
    }

    function deleteViewed() {
        if (!state.viewing) return;
        if (!confirm('Permanently delete "' + conversationTitle(state.viewing) + '"?')) return;
        $.ajax({
            url: '/api/conversations/' + state.viewing.id,
            method: 'DELETE',
            success: showLive,
            error: function () {
                alert('Could not delete the conversation.');
            },
        });
    }

    // ── SSE Connection ──────────────────────────────────────────────

    function connectSSE() {
        if (state.evtSource) {
            state.evtSource.close();
        }

        var evtSource = new EventSource('/api/stream');
        state.evtSource = evtSource;

        // While an archived conversation is open, live traffic is ignored here and
        // the live chat is re-fetched on the way back (see showLive).

        evtSource.addEventListener('new_message', function (e) {
            var msg = JSON.parse(e.data);
            if (msg.type === 'operator') refreshConversations();
            if (state.viewing) return;
            if (!state.messages.length) $('#messages').empty();
            state.messages.push(msg);
            appendMessage(msg);
        });

        evtSource.addEventListener('stream_start', function (e) {
            if (state.viewing) return;
            var msg = JSON.parse(e.data);
            state.pendingStreams[msg.id] = msg;
        });

        evtSource.addEventListener('stream_token', function (e) {
            if (state.viewing) return;
            var data = JSON.parse(e.data);
            var $content = $('#content-' + data.id);
            if (!$content.length) {
                var pending = state.pendingStreams[data.id];
                if (pending) {
                    if (!state.messages.length) $('#messages').empty();
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
            if (state.viewing) return;
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

        evtSource.addEventListener('conversation_changed', function (e) {
            state.liveConversation = JSON.parse(e.data);
            if (state.viewing) {
                refreshConversations();
            } else {
                showLive();
            }
        });

        evtSource.addEventListener('conversations_updated', function () {
            refreshConversations();
        });

        var lostConnection = false;
        evtSource.onerror = function () {
            lostConnection = true;
            console.warn('SSE connection lost, reconnecting...');
        };
        evtSource.onopen = function () {
            // Events may have been missed while disconnected; resync
            if (lostConnection) {
                lostConnection = false;
                if (!state.viewing) loadLive();
                refreshConversations();
            }
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
        // Load the live conversation and the archive list
        loadLive();
        refreshConversations();

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

        // Conversation controls
        $('#btn-new-conversation').on('click', startNewConversation);
        $('#btn-archive-back').on('click', showLive);
        $('#btn-archive-rename').on('click', renameViewed);
        $('#btn-archive-bots').on('click', function () {
            $('#archive-bots').slideToggle(150);
        });
        $('#btn-archive-delete').on('click', deleteViewed);
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
