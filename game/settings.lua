-- 仅在构建时将已校验的共享语言目录注入本模块。
local catalogue = {
--[[HD2CT_TARGET_LANGUAGE_CATALOGUE]]
}

local function new_registration_step()
    local language_choices = {}
    for index, language in ipairs(catalogue.languages) do
        language_choices[index] = language.label
    end

    local timeout_choices = {}
    for index, choice in ipairs(catalogue.menu_options.timeout.choices) do
        timeout_choices[index] = choice.label
    end

    local specs = {
        {
            option_id = catalogue.option_id,
            spec = {
                type = "choice",
                label = "目标语言 / Target Language",
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.default_index,
                choices = language_choices,
                description = "APPLY 后开始处理的任务使用新目标语言。",
            },
        },
        {
            option_id = catalogue.menu_options.enabled.option_id,
            spec = {
                type = catalogue.menu_options.enabled.type,
                label = catalogue.menu_options.enabled.label,
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.menu_options.enabled.default,
                description = catalogue.menu_options.enabled.description,
            },
        },
        {
            option_id = catalogue.menu_options.timeout.option_id,
            spec = {
                type = catalogue.menu_options.timeout.type,
                label = catalogue.menu_options.timeout.label,
                mod = "HD2 Chat Translate",
                mod_id = catalogue.mod_id,
                default = catalogue.menu_options.timeout.default_index,
                choices = timeout_choices,
                description = catalogue.menu_options.timeout.description,
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
