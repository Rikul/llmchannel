// ── IRC Chat – Settings Modal ───────────────────────────────────────

var Settings = (function () {

    var presets = {};          // name -> base URL, loaded from /api/settings
    var defaultPrompt = '';
    var expanded = {};         // bot id -> true while its card is open
    var knownNicknames = [];

    var modelHints = {
        'openrouter.ai': [
            'openai/gpt-4o-mini', 'anthropic/claude-sonnet-4.5', 'google/gemini-2.5-flash',
            'deepseek/deepseek-chat', 'meta-llama/llama-3.3-70b-instruct',
        ],
        'localhost:11434': ['llama3.2', 'qwen2.5', 'gemma2', 'mistral'],
    };

    function open() {
        $('#settings-modal').css('display', 'flex');
        loadChannelSettings();
        loadBots();
    }

    function close() {
        $('#settings-modal').css('display', 'none');
    }

    // ── Channel Settings ────────────────────────────────────────────

    function loadChannelSettings() {
        $.getJSON('/api/settings', function (data) {
            presets = data.endpoint_presets || {};
            defaultPrompt = data.default_system_prompt || '';
            $('#channel-system-prompt').val(data.system_prompt);
            $('#channel-chain-limit').val(data.bot_chain_limit);
        });
    }

    function saveChannelSettings() {
        $.ajax({
            url: '/api/settings',
            method: 'PUT',
            contentType: 'application/json',
            data: JSON.stringify({
                system_prompt: $('#channel-system-prompt').val(),
                bot_chain_limit: $('#channel-chain-limit').val(),
            }),
            success: function (data) {
                $('#channel-system-prompt').val(data.system_prompt);
                $('#channel-chain-limit').val(data.bot_chain_limit);
                showToast('Channel settings saved');
            },
            error: function (xhr) {
                showToast(errorText(xhr, 'Failed to save channel settings'), true);
            },
        });
    }

    // ── Bot Cards ───────────────────────────────────────────────────

    function loadBots() {
        $.getJSON('/api/bots', function (bots) {
            var $list = $('#bot-config-list');
            $list.empty();
            knownNicknames = $.map(bots, function (b) { return b.nickname.toLowerCase(); });
            $.each(bots, function (i, bot) {
                $list.append(createBotCard(bot));
            });
        });
    }

    function endpointLabel(url) {
        var label = '';
        $.each(presets, function (name, presetUrl) {
            if (presetUrl === url) label = name;
        });
        return label;
    }

    function hintsFor(baseUrl) {
        var hints = [];
        $.each(modelHints, function (host, models) {
            if ((baseUrl || '').indexOf(host) !== -1) hints = models;
        });
        return hints;
    }

    function buildOptions(values) {
        var html = '';
        $.each(values, function (i, v) {
            html += '<option value="' + escapeHtml(v) + '">';
        });
        return html;
    }

    function presetOptions() {
        var html = '';
        $.each(presets, function (name, url) {
            html += '<option value="' + escapeHtml(url) + '">' + escapeHtml(name) + '</option>';
        });
        return html;
    }

    function numberValue(v) {
        return (v === null || v === undefined) ? '' : v;
    }

    function createBotCard(bot) {
        var $card = $('<div class="bot-card"></div>').attr('data-bot-id', bot.id);
        var $body = $('<div class="bot-card-body"></div>').toggle(!!expanded[bot.id]);
        var subtitle = bot.model + (endpointLabel(bot.base_url) ?
            ' · ' + endpointLabel(bot.base_url) : '');

        // Header row with nickname, enabled toggle, delete
        var $header = $(
            '<div style="display: flex; align-items: center; gap: 12px; margin-bottom: 12px; ' +
            'cursor: pointer;">' +
            '<button class="icon-btn btn-toggle-bot" title="Expand" ' +
            'style="width: 24px; height: 24px;">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
            '<polyline points="6 9 12 15 18 9"/>' +
            '</svg>' +
            '</button>' +
            '<div style="flex: 1; min-width: 0;">' +
            '<div style="font-weight: 600; font-size: 0.9375rem; color: ' +
            escapeHtml(bot.color) + ';">' + escapeHtml(bot.nickname) + '</div>' +
            '<div style="font-size: 0.6875rem; color: var(--text-muted); overflow: hidden; ' +
            'text-overflow: ellipsis; white-space: nowrap;">' + escapeHtml(subtitle) + '</div>' +
            '</div>' +
            '<input type="checkbox" class="toggle-switch bot-enabled" title="Enabled" ' +
            (bot.enabled ? 'checked' : '') + '>' +
            '<button class="btn btn-danger btn-sm btn-delete-bot" title="Delete bot">' +
            'Delete</button>' +
            '</div>'
        );
        $card.append($header);

        var urlListId = 'endpoints-' + bot.id;
        var modelListId = 'models-' + bot.id;

        $body.append(
            // Nickname + Color
            '<div style="display: grid; grid-template-columns: 1fr auto; gap: 12px;">' +
            '<div class="field-group"><label>Nickname</label>' +
            '<input type="text" class="bot-nickname" maxlength="32" value="' +
            escapeHtml(bot.nickname) + '"></div>' +
            '<div class="field-group"><label>Color</label>' +
            '<input type="color" class="bot-color" value="' + escapeHtml(bot.color) + '">' +
            '</div>' +
            '</div>' +

            // Endpoint
            '<div class="field-group"><label>Endpoint URL</label>' +
            '<input type="text" class="bot-base-url" list="' + urlListId + '" value="' +
            escapeHtml(bot.base_url) + '" placeholder="https://openrouter.ai/api/v1">' +
            '<datalist id="' + urlListId + '">' + presetOptions() + '</datalist>' +
            '<div class="field-hint">Any OpenAI-compatible <code>/v1</code> base URL ' +
            '(OpenRouter, Ollama, LM Studio, llama.cpp, vLLM…).</div>' +
            '</div>' +

            // API key
            '<div class="field-group"><label>API Key</label>' +
            '<input type="password" class="bot-api-key" autocomplete="off" value="' +
            escapeHtml(bot.api_key) + '" placeholder="Leave empty for local servers">' +
            '</div>' +

            // Model
            '<div class="field-group"><label>Model</label>' +
            '<div class="field-row">' +
            '<input type="text" class="bot-model" list="' + modelListId + '" value="' +
            escapeHtml(bot.model) + '" placeholder="e.g. openai/gpt-4o-mini or llama3.2">' +
            '<button class="btn btn-ghost btn-sm btn-fetch-models" ' +
            'title="Ask the endpoint which models it serves">Fetch models</button>' +
            '</div>' +
            '<datalist id="' + modelListId + '">' + buildOptions(hintsFor(bot.base_url)) +
            '</datalist>' +
            '</div>' +

            // Temperature + Max tokens
            '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">' +
            '<div class="field-group"><label>Temperature</label>' +
            '<input type="number" class="bot-temperature" min="0" max="2" step="0.1" ' +
            'placeholder="Model default" value="' + numberValue(bot.temperature) + '"></div>' +
            '<div class="field-group"><label>Max tokens</label>' +
            '<input type="number" class="bot-max-tokens" min="1" step="1" ' +
            'placeholder="Model default" value="' + numberValue(bot.max_tokens) + '"></div>' +
            '</div>' +

            // Persona
            '<div class="field-group"><label>Persona</label>' +
            '<textarea class="bot-persona" rows="4" placeholder="Who this bot is: role, ' +
            'expertise, tone, quirks. Combined with the main system prompt.">' +
            escapeHtml(bot.persona) + '</textarea>' +
            '</div>' +

            // Save
            '<div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px;">' +
            '<button class="btn btn-primary btn-sm btn-save-bot">Save</button>' +
            '</div>'
        );

        $card.append($body);

        // ── Event handlers ──

        function toggleBody() {
            expanded[bot.id] = !expanded[bot.id];
            $body.slideToggle(150);
        }

        $header.on('click', function (e) {
            if ($(e.target).closest('button, input, select, textarea, label').length) {
                return;
            }
            toggleBody();
        });

        $card.find('.btn-toggle-bot').on('click', function (e) {
            e.stopPropagation();
            toggleBody();
        });

        // Enable/disable takes effect immediately
        $card.find('.bot-enabled').on('change', function () {
            setEnabled(bot.id, $(this).is(':checked'));
        });

        // Endpoint change -> refresh static model hints
        $card.find('.bot-base-url').on('change', function () {
            $card.find('#' + modelListId).html(buildOptions(hintsFor($(this).val())));
        });

        $card.find('.btn-fetch-models').on('click', function () {
            fetchModels(bot.id, $card, $(this));
        });

        $card.find('.btn-save-bot').on('click', function () {
            saveBot(bot.id, $card);
        });

        $card.find('.btn-delete-bot').on('click', function () {
            if (confirm('Remove bot "' + bot.nickname + '"?')) {
                deleteBot(bot.id);
            }
        });

        return $card;
    }

    function escapeHtml(text) {
        if (text === null || text === undefined) return '';
        var div = document.createElement('div');
        div.appendChild(document.createTextNode(String(text)));
        return div.innerHTML.replace(/"/g, '&quot;');
    }

    function errorText(xhr, fallback) {
        return (xhr && xhr.responseJSON && xhr.responseJSON.error) || fallback;
    }

    // ── Bot API Calls ───────────────────────────────────────────────

    function fetchModels(botId, $card, $btn) {
        var modelListId = 'models-' + botId;
        $btn.prop('disabled', true).text('Fetching…');
        $.ajax({
            url: '/api/models',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({
                bot_id: botId,
                base_url: $card.find('.bot-base-url').val(),
                api_key: $card.find('.bot-api-key').val(),
            }),
            success: function (data) {
                var models = data.models || [];
                $card.find('#' + modelListId).html(buildOptions(models));
                showToast(models.length + ' models available — start typing to search');
                $card.find('.bot-model').trigger('focus');
            },
            error: function (xhr) {
                showToast(errorText(xhr, 'Could not fetch models'), true);
            },
            complete: function () {
                $btn.prop('disabled', false).text('Fetch models');
            },
        });
    }

    function saveBot(botId, $card) {
        var data = {
            nickname: $card.find('.bot-nickname').val(),
            color: $card.find('.bot-color').val(),
            base_url: $card.find('.bot-base-url').val(),
            api_key: $card.find('.bot-api-key').val(),
            model: $card.find('.bot-model').val(),
            temperature: $card.find('.bot-temperature').val(),
            max_tokens: $card.find('.bot-max-tokens').val(),
            persona: $card.find('.bot-persona').val(),
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
            error: function (xhr) {
                showToast(errorText(xhr, 'Failed to save bot'), true);
            },
        });
    }

    function setEnabled(botId, enabled) {
        $.ajax({
            url: '/api/bots/' + botId + '/toggle',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ enabled: enabled }),
            success: function () {
                ChatApp.refreshSidebar();
                showToast(enabled ? 'Bot enabled' : 'Bot disabled');
            },
            error: function (xhr) {
                showToast(errorText(xhr, 'Failed to update bot'), true);
                loadBots();
            },
        });
    }

    function deleteBot(botId) {
        $.ajax({
            url: '/api/bots/' + botId,
            method: 'DELETE',
            success: function () {
                delete expanded[botId];
                loadBots();
                ChatApp.refreshSidebar();
                showToast('Bot removed');
            },
            error: function (xhr) {
                showToast(errorText(xhr, 'Failed to remove bot'), true);
            },
        });
    }

    function nextNickname() {
        var n = knownNicknames.length + 1;
        while ($.inArray(('bot' + n).toLowerCase(), knownNicknames) !== -1) {
            n++;
        }
        return 'Bot' + n;
    }

    function addBot() {
        $.ajax({
            url: '/api/bots',
            method: 'POST',
            contentType: 'application/json',
            data: JSON.stringify({ nickname: nextNickname() }),
            success: function (bot) {
                expanded[bot.id] = true;
                loadBots();
                ChatApp.refreshSidebar();
                showToast('Bot added (disabled until you enable it)');
            },
            error: function (xhr) {
                showToast(errorText(xhr, 'Failed to add bot'), true);
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
        $('#btn-save-channel').on('click', saveChannelSettings);
        $('#btn-reset-prompt').on('click', function () {
            if (confirm('Replace the main system prompt with the default? (Not saved yet)')) {
                $('#channel-system-prompt').val(defaultPrompt);
            }
        });

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
