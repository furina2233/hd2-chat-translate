-- 仅在构建时将已校验的共享语言目录注入本模块。
local catalogue = {
--[[HD2CT_TARGET_LANGUAGE_CATALOGUE]]
}

local menu_locales = {
--[[HD2CT_MENU_LOCALES]]
}

local function current_locale()
    local translations = rawget(_G, "BingusTranslations")
    if type(translations) ~= "table" or rawget(translations, "version") ~= 1 then
        return rawget(menu_locales, "en")
    end

    local game_language = rawget(translations, "game_language")
    if type(game_language) ~= "string" then
        return rawget(menu_locales, "en")
    end

    local locale = rawget(menu_locales, game_language)
    if type(locale) == "table" then return locale end

    local base_language = string.match(game_language, "^([^-]+)")
    if type(base_language) == "string" then
        locale = rawget(menu_locales, base_language)
        if type(locale) == "table" then return locale end
    end
    return rawget(menu_locales, "en")
end

local function localized_text(key, index, seconds)
    local locale = current_locale()
    local value
    if key == "target_language_choices" then
        local choices = rawget(locale, key)
        if type(choices) == "table" then value = rawget(choices, index) end
    else
        value = rawget(locale, key)
    end
    if type(value) ~= "string" then
        locale = rawget(menu_locales, "en")
        if key == "target_language_choices" then
            value = rawget(rawget(locale, key), index)
        else
            value = rawget(locale, key)
        end
    end

    if key == "timeout_choice_format" then
        if seconds ~= 10 and seconds ~= 20 and seconds ~= 30 then seconds = 20 end
        local rendered = string.gsub(value, "{seconds}", string.format("%d", seconds), 1)
        return rendered
    end
    return value
end

local function text_callback(key, index, seconds)
    return function()
        return localized_text(key, index, seconds)
    end
end

local function new_registration_step()
    local language_choices = {}
    for index in ipairs(catalogue.languages) do
        language_choices[index] = text_callback("target_language_choices", index)
    end

    local timeout_choices = {}
    for index, choice in ipairs(catalogue.menu_options.timeout.choices) do
        timeout_choices[index] = text_callback("timeout_choice_format", nil, choice.seconds)
    end

    local specs = {
        {
            option_id = catalogue.option_id,
            spec = {
                type = "choice",
                label = text_callback("target_language_label"),
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.default_index,
                choices = language_choices,
                description = text_callback("target_language_description"),
            },
        },
        {
            option_id = catalogue.menu_options.enabled.option_id,
            spec = {
                type = catalogue.menu_options.enabled.type,
                label = text_callback("enabled_label"),
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.menu_options.enabled.default,
                description = text_callback("enabled_description"),
            },
        },
        {
            option_id = catalogue.menu_options.timeout.option_id,
            spec = {
                type = catalogue.menu_options.timeout.type,
                label = text_callback("timeout_label"),
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.menu_options.timeout.default_index,
                choices = timeout_choices,
                description = text_callback("timeout_description"),
            },
        },
        {
            option_id = catalogue.menu_options.outgoing_enabled.option_id,
            spec = {
                type = catalogue.menu_options.outgoing_enabled.type,
                label = text_callback("outgoing_enabled_label"),
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.menu_options.outgoing_enabled.default,
                description = text_callback("outgoing_enabled_description"),
            },
        },
        {
            option_id = catalogue.menu_options.outgoing_target.option_id,
            spec = {
                type = catalogue.menu_options.outgoing_target.type,
                label = text_callback("outgoing_target_label"),
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.menu_options.outgoing_target.default_index,
                choices = language_choices,
                description = text_callback("outgoing_target_description"),
            },
        },
    }
    local registered = false
    local next_option = 1
    local next_attempt_ms = 0
    local failure_logged = false

    return function(now_ms)
        if registered then return true end
        if type(now_ms) ~= "number" or now_ms ~= now_ms or now_ms < 0
            or now_ms == math.huge or now_ms % 1 ~= 0 then
            return false
        end
        if now_ms < next_attempt_ms then return false end
        next_attempt_ms = now_ms + 1000

        local menu = rawget(_G, "ModOptionsMenu")
        local version = type(menu) == "table" and rawget(menu, "version") or nil
        if type(menu) ~= "table" or rawget(menu, "api") ~= 1
            or type(version) ~= "number" or version < 3 then return false end
        local register_option = rawget(menu, "register_option")
        if type(register_option) ~= "function" then return false end

        while next_option <= #specs do
            local item = specs[next_option]
            local ok, result = pcall(register_option, item.option_id, item.spec)
            if not ok or result ~= true then
                if not failure_logged then
                    failure_logged = true
                    pcall(print, "[HD2 Chat Translate] menu option registration failed")
                end
                return false
            end
            next_option = next_option + 1
        end
        registered = true
        return true
    end
end

local function wrap_update(previous_update, registration_step, clock)
    local registration_complete = false
    return function(...)
        if not registration_complete then
            local clock_ok, now_ms = pcall(clock)
            if clock_ok then
                local step_ok, done = pcall(registration_step, now_ms)
                if step_ok and done == true then registration_complete = true end
            end
        end
        if type(previous_update) == "function" then return previous_update(...) end
    end
end

return {new = new_registration_step, wrap_update = wrap_update}
