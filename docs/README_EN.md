# HD2 Chat Translate

A chat window translation plugin for Helldivers 2. It supports AI translation and machine translation into multiple languages. Translations are not sent to other players.

The currently compatible versions are Steam build 25480438 and game EXE 1.8.46015.0. A game update may cause this plugin to stop working.

> **Usage risk:** The plugin injects into the game process, executes code, and calls internal game functions to update the chat display. This behavior may violate the game’s or anti-cheat rules and may result in account penalties or a ban.

English documentation: [README_EN.md](README_EN.md)

## Installation and updates

1. Close the game normally and import the mod into HD2Arsenal.
2. Click Deploy, then launch the game. The standalone version requires both prerequisite mods, Bingus Shared Loader and Mod Options Menu, to be installed. The dependency-bundled version does not.
3. To update the mod, close the game, import the new ZIP, update the mod with the same name, and deploy again.

## Configuration

Supported translation providers:

- AI translation: any provider that offers a Chat Completions endpoint and supports JSON mode
- Machine translation: Google, Baidu, Youdao

Enter the following values in Windows Environment Variables:

| Variable | Value | Required | Default |
| --- | --- | --- | --- |
| HD2CT_API_URL | Endpoint URL for the selected translation service | Yes | None |
| HD2CT_MODEL | AI model name; delete it or leave it empty for machine translation | Required for AI translation | None |
| HD2CT_API_KEY | API key for AI/Google, or application secret for Baidu/Youdao | Yes | None |
| HD2CT_APP_ID | Baidu APP ID or Youdao application ID | Required for Baidu/Youdao | None |

When the model name is non-empty, the plugin uses the AI Chat Completions endpoint and can complete the endpoint URL automatically. When the model name is missing or empty, it uses machine translation and can also complete the endpoint URL automatically. The supported machine translation providers are Google, Baidu, and Youdao.

| Machine translation service | Full endpoint URL | Credentials |
| --- | --- | --- |
| [Google Cloud Translation Basic v2](https://docs.cloud.google.com/translate/docs/reference/rest/v2/translate) | `https://translation.googleapis.com/language/translate/v2` | HD2CT_API_KEY |
| [Baidu General Translation](https://fanyi-api.baidu.com/doc_bd/21) | `https://fanyi-api.baidu.com/api/trans/vip/translate` | HD2CT_APP_ID, HD2CT_API_KEY |
| [Youdao Text Translation](https://ai.youdao.com/DOCSIRMA/html/trans/api/wbfy/index.html) | `https://openapi.youdao.com/api` | HD2CT_APP_ID, HD2CT_API_KEY |

When using machine translation, a translation line is shown even when the source and target languages are the same. With AI translation, no translation line is shown when the source and target languages are the same.

Configuration is read from Windows user variables first, then system variables.
When switching to machine translation, make sure neither the user nor system variables contain a non-empty model setting, or override the system model setting with an empty user model value.

The plugin reads configuration once when the game starts. Changes, additions, or deletions take effect only after restarting the game. Environment variables are stored in plain text; enter secrets only on your local machine. When translation is enabled, chat text is sent to the configured provider.

You can make other adjustments in the settings page:

| Setting | Description |
| --- | --- |
| Target language | Language of the translation |
| Enabled | Whether translation is enabled |
| Timeout | Request timeout duration |

Note that the plugin monitors the chat window whether or not translation is enabled. To remove it completely, uninstall it in the mod manager.

## Visible notices and basic troubleshooting

For common request failures, the original text is preserved and one of the following notices is shown in the translation position:

| Situation | Displayed message |
| --- | --- |
| HTTP 400 or 422 | Invalid request parameters |
| HTTP 401 | API key is invalid |
| HTTP 403 | You are not authorized to use this endpoint |
| HTTP 404 | Endpoint or model does not exist |
| Timeout | Request timed out |
| HTTP 429 | Too many requests; try again later |
| Other HTTP 5xx | Service temporarily unavailable |
| Network connection failure | Network connection failed |
| Invalid or overly long response | Response is invalid or too long |
| Invalid configuration or URL | Model configuration or endpoint URL is invalid |
| Machine translation URL does not match a service | This translation service is not currently supported |
| Invalid application credentials or signature | Translation credentials or signature are invalid |
| Insufficient translation account quota | Translation quota is insufficient |
| Service does not support the source language | This language is not supported |
| Other failure | Translation service error or translation failed; try again later |

If no translation appears, first confirm that the mod is enabled in Arsenal and deployed. Then check the variables required by the selected service and whether they are set as user or system variables. Restart the game and send a new non-Chinese chat message. In AI mode, the service may identify the source as Chinese; in that case, only the original text is kept. For machine translation behavior with Chinese messages, see above. Chats sent before startup are not translated retroactively. In AI mode, check the model name, endpoint path, and JSON mode support. For machine translation, check API access, the application ID, key, and account quota. Youdao signatures also depend on the system clock being correct. For network notices, check your local connection and provider availability.

## Local logs

Logs are stored in `%LOCALAPPDATA%\HD2ChatTranslate`: `probe` contains startup verification reports, `observe` contains observation reports, and `mailbox` contains translation status reports. At startup, the plugin removes older files of each report type based on last-modified time, keeps at most 10 per type, and reserves a slot for a new report for the current session.

Cleanup runs only at startup. Configuration, the DLL cache, communication files, and temporary files are retained. Reports that are in use or cannot be deleted are skipped and retried at the next startup. Cleanup failures do not affect translation.

## Disable and uninstall

Set `HD2CT_ENABLED` to `0` and restart the game to disable translation. To uninstall, close the game normally, disable or remove this mod in Arsenal, and deploy again. If other mods depend on Shared Loader, keep or restore their respective loaders after uninstalling this bundled package. After the game is closed, you can delete the plugin-specific local DLL cache and remove the environment variables yourself.

## Documentation

- [Current implementation and technical boundaries](technical.md)
- [Manual build guide](build.md)

## License

Original code in this project is licensed under GNU GPL v3.0 only (SPDX: GPL-3.0-only); see [LICENSE](../LICENSE) for the full text. Third-party cJSON remains under the MIT License. Bingus Shared Loader v18 in the bundled package follows its upstream license and source notices; this project does not relicense that upstream content.
