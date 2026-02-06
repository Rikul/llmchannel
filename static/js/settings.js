// ── IRC Chat – Settings Modal ───────────────────────────────────────

var Settings = (function () {

    var modelHints = {
        openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
        anthropic: ['claude-sonnet-4-5-20250929', 'claude-haiku-4-20250414', 'claude-opus-4-20250514'],
        gemini: ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.0-flash'],
    };

    function open() {
        $('#settings-modal').css('display', 'flex');
        loadBots();
    }

    function close() {
        $('#settings-modal').css('display', 'none');
    }

    function loadBots() {
        $.getJSON('/api/bots', function (bots) {
            var $list = $('#bot-config-list');
            $list.empty();
            $.each(bots, function (i, bot) {
                $list.append(createBotCard(bot));
            });
        });
    }

    function createBotCard(bot) {
        var $card = $('<div class="bot-card" data-bot-id="' + bot.id + '"></div>');
        var $body = $('<div class="bot-card-body" style="display: none;"></div>');

        // Header row with nickname, toggle, delete
        var $header = $(
            '<div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px;">' +
            '<button class="icon-btn btn-toggle-bot" title="Expand" style="width: 24px; height: 24px;">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<polyline points="6 9 12 15 18 9"/>' +
            '</svg>' +
            '</button>' +
            '<div style="flex: 1; font-weight: 600; font-size: 0.9375rem; color: ' + escapeHtml(bot.color) + ';">' +
            escapeHtml(bot.nickname) +
            '</div>' +
            '<label style="display: flex; align-items: left; gap: 6px; font-size: 0.25rem; color: var(--text-secondary); cursor: pointer; flex-shrink: 0; white-space: nowrap;">' +
            '<input type="checkbox" class="toggle-switch bot-enabled" ' + (bot.enabled ? 'checked' : '') + '>' +
            '</label>' +
            '<button class="btn btn-danger btn-sm btn-delete-bot" title="Delete bot">Delete</button>' +
            '</div>'
        );
        $card.append($header);

        // Row 1: Nickname + Color
        $body.append(
            '<div style="display: grid; grid-template-columns: 1fr auto; gap: 12px;">' +
            '<div class="field-group">' +
            '<label>Nickname</label>' +
            '<input type="text" class="bot-nickname" value="' + escapeHtml(bot.nickname) + '">' +
            '</div>' +
            '<div class="field-group">' +
            '<label>Color</label>' +
            '<input type="color" class="bot-color" value="' + escapeHtml(bot.color) + '">' +
            '</div>' +
            '</div>'
        );

        // Row 2: Provider + Model
        var providerOptions = ['openai', 'anthropic', 'gemini'];
        var selectHtml = '<select class="bot-provider">';
        $.each(providerOptions, function (i, p) {
            var sel = (p === bot.provider) ? ' selected' : '';
            selectHtml += '<option value="' + p + '"' + sel + '>' + p.charAt(0).toUpperCase() + p.slice(1) + '</option>';
        });
        selectHtml += '</select>';

        $body.append(
            '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">' +
            '<div class="field-group">' +
            '<label>Provider</label>' +
            selectHtml +
            '</div>' +
            '<div class="field-group">' +
            '<label>Model</label>' +
            '<input type="text" class="bot-model" value="' + escapeHtml(bot.model) + '" list="models-' + bot.id + '">' +
            '<datalist id="models-' + bot.id + '">' +
            buildModelOptions(bot.provider) +
            '</datalist>' +
            '</div>' +
            '</div>'
        );

        // API Key
        $body.append(
            '<div class="field-group">' +
            '<label>API Key</label>' +
            '<input type="password" class="bot-api-key" value="' + escapeHtml(bot.api_key) + '" placeholder="Enter API key...">' +
            '</div>'
        );

        // Gemini OAuth
        $body.append(
            '<div class="field-group bot-gemini-oauth" style="display: ' +
            (bot.provider === 'gemini' ? 'block' : 'none') + ';">' +
            '<label>Gemini OAuth</label>' +
            '<div style="display: flex; align-items: center; gap: 10px;">' +
            '<a class="btn btn-ghost btn-sm" href="/api/bots/' + bot.id + '/google/login" target="_blank" rel="noopener">' +
            'Login with Google</a>' +
            '<span class="bot-gemini-status" style="font-size: 0.75rem; color: var(--text-muted);">' +
            (bot.gemini_access_token ? 'Connected' : 'Not connected') +
            '</span>' +
            '</div>' +
            '</div>'
        );

        // System Prompt
        $body.append(
            '<div class="field-group">' +
            '<label>System Prompt</label>' +
            '<textarea class="bot-system-prompt" rows="3">' + escapeHtml(bot.system_prompt) + '</textarea>' +
            '</div>'
        );

        // Save button
        $body.append(
            '<div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px;">' +
            '<button class="btn btn-primary btn-sm btn-save-bot">Save</button>' +
            '</div>'
        );

        $card.append($body);

        // ── Event handlers ──

        // Collapse/expand
        $header.on('click', function (e) {
            if ($(e.target).closest('button, input, select, textarea, label').length) {
                return;
            }
            $body.slideToggle(150);
        });

        $card.find('.btn-toggle-bot').on('click', function (e) {
            e.stopPropagation();
            $body.slideToggle(150);
        });

        // Provider change -> update model hints
        $card.find('.bot-provider').on('change', function () {
            var provider = $(this).val();
            var hints = modelHints[provider] || [];
            var $modelGroup = $card.find('.bot-model').closest('.field-group');
            var currentValue = $card.find('.bot-model').val();
            var nextValue = '';

            if ($.inArray(currentValue, hints) !== -1) {
                nextValue = currentValue;
            } else if (hints.length) {
                nextValue = hints[0];
            }

            var listId = 'models-' + bot.id + '-' + Date.now();
            $modelGroup.find('.bot-model').remove();
            $modelGroup.find('datalist').remove();
            $modelGroup.append(
                '<input type="text" class="bot-model" value="' +
                escapeHtml(nextValue) +
                '" list="' + listId + '">' +
                '<datalist id="' + listId + '">' +
                buildModelOptions(provider) +
                '</datalist>'
            );

            $card.find('.bot-gemini-oauth').toggle(provider === 'gemini');
        });

        // Save
        $card.find('.btn-save-bot').on('click', function () {
            saveBot(bot.id, $card);
        });

        // Delete
        $card.find('.btn-delete-bot').on('click', function () {
            if (confirm('Remove bot "' + bot.nickname + '"?')) {
                deleteBot(bot.id);
            }
        });

        return $card;
    }

    function buildModelOptions(provider) {
        var hints = modelHints[provider] || [];
        var html = '';
        $.each(hints, function (i, m) {
            html += '<option value="' + m + '">';
        });
        return html;
    }

    function escapeHtml(text) {
        if (!text) return '';
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(text));
        return div.innerHTML;
    }

    function saveBot(botId, $card) {
        var data = {
            nickname: $card.find('.bot-nickname').val(),
            provider: $card.find('.bot-provider').val(),
            model: $card.find('.bot-model').val(),
            api_key: $card.find('.bot-api-key').val(),
            system_prompt: $card.find('.bot-system-prompt').val(),
            color: $card.find('.bot-color').val(),
            enabled: $card.find('.bot-enabled').is(':checked'),
        };

        $.ajax({
            url: '/api/bots/' + botId,
            method: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify(data),
            success: function () {
                loadBots();
                ChatApp.refreshSidebar();
                showToast('Bot saved');
            },
            error: function () {
                showToast('Failed to save bot', true);
            },
        });
    }

    function deleteBot(botId) {
        $.ajax({
            url: '/api/bots/' + botId,
            method: 'DELETE',
            success: function () {
                loadBots();
                ChatApp.refreshSidebar();
                showToast('Bot removed');
            },
        });
    }

    function addBot() {
        $.ajax({
            url: '/api/bots',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                nickname: 'NewBot',
                provider: 'openai',
                model: 'gpt-4o-mini',
                api_key: '',
                system_prompt: 'You are a helpful assistant participating in a group chat channel.',
                color: '#6366f1',
            }),
            success: function () {
                loadBots();
                ChatApp.refreshSidebar();
                showToast('Bot added');
            },
        });
    }

    function showToast(message, isError) {
        var $toast = $('<div></div>')
            .text(message)
            .css({
                position: 'fixed',
                bottom: '20px',
                right: '20px',
                padding: '10px 20px',
                borderRadius: '8px',
                backgroundColor: isError ? '#ef4444' : '#22c55e',
                color: 'white',
                fontSize: '0.8125rem',
                fontWeight: '500',
                fontFamily: "'Inter', sans-serif",
                zIndex: 100,
                boxShadow: '0 4px 12px rgba(0,0,0,0.2)',
                opacity: 0,
            })
            .appendTo('body')
            .animate({ opacity: 1 }, 200);

        setTimeout(function () {
            $toast.animate({ opacity: 0 }, 300, function () {
                $toast.remove();
            });
        }, 2000);
    }

    // ── Bind events after DOM ready ─────────────────────────────────

    $(function () {
        $('#btn-settings').on('click', open);
        $('#btn-close-settings').on('click', close);
        $('#btn-add-bot').on('click', addBot);

        // Close on backdrop click
        $('#settings-modal').on('click', function (e) {
            if ($(e.target).is('#settings-modal')) {
                close();
            }
        });

        // Close on Escape
        $(document).on('keydown', function (e) {
            if (e.key === 'Escape' && $('#settings-modal').css('display') !== 'none') {
                close();
            }
        });
    });

    return {
        open: open,
        close: close,
        refresh: loadBots,
    };
})();
