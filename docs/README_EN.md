# HD2 Chat Translate

A chat window translation plugin for Helldivers 2. It supports AI and machine translation into multiple languages. Translations are not sent to other players.

The current compatibility scope is Steam build 25480438 and game executable (EXE) 1.8.46015.0. A game update may cause this plugin to stop working.

> **Usage risk:** The plugin injects into the game process, executes code, and calls internal game functions to update the chat display. This behavior may violate the game's or anti-cheat rules and may result in account penalties or a ban.

English documentation: [README_EN.md](README_EN.md)

## Installation and updates

1. Close the game normally and import the mod into HD2Arsenal.
2. Click Deploy. You must have both prerequisite mods, `Bingus Shared Loader` and `Mod Options Menu`, installed.
3. Start the game.

## Free, ready-to-use translation

After installing this plugin, you can use it for free immediately without configuring any environment variables.

The free version is provided by Bing Translate and is not guaranteed to remain available.

## Configuration

Supported translation providers:

- Free translation: ready to use without configuration. You do not need to understand the configuration instructions below to use it.
- AI translation: providers that offer a Chat Completions or Anthropic endpoint and support JSON mode.
- Machine translation: Google, Baidu, Youdao, Bing China, and Bing global.

Enter the following values in Windows Environment Variables:

| Variable | Value | Required |
| --- | --- | --- |
| HD2CT_API_URL | Endpoint URL for the selected AI provider | Required for AI translation |
| HD2CT_MODEL | AI model name | Required for AI translation |
| HD2CT_API_KEY | API key for AI/Google, or application secret for Baidu/Youdao | Yes |
| HD2CT_APP_ID | Baidu APP ID or Youdao application ID | Required for Baidu/Youdao |

Notes:

- When the source and target languages are the same, the translation line will not be displayed. This is not a bug.
- Changes to environment variables take effect after restarting the game.

You can make other adjustments in the settings page:

| Setting | Description |
| --- | --- |
| Target language | Language of the translation |
| Enabled | Whether translation is enabled |
| Do not translate my messages | When enabled, messages sent by you will not be translated |
| Timeout | Request timeout duration |
| Interface type | Type of translation service interface |
| Translate enemy names colloquially | Only applies to AI translation when the target language is Chinese; translates enemy names colloquially |

Notes:

- The plugin monitors the chat window whether or not translation is enabled. To remove it completely, uninstall it in the mod manager.
- Changes made in the settings page take effect immediately and do not require a game restart.

## Visible notices and basic troubleshooting

For common request failures, the original text is preserved and one of the following notices is shown in the translation position:

| Situation | Displayed message | Basic troubleshooting |
| --- | --- | --- |
| Required configuration is missing for the selected interface | Translation configuration is incomplete | Check the in-game “Interface type” setting. AI translation requires a URL, model name, and key; machine translation requires a key; Baidu and Youdao also require an app ID. The free version does not require environment variables. |
| A configuration value has an invalid format | Invalid configuration | Check for extra whitespace, quotation marks, or excessive length. Bing's Region should match the resource region. |
| The AI endpoint URL is invalid | Endpoint URL is invalid | Use an HTTPS URL without a key, query parameters, or fragment. Machine translation URLs are built into the plugin. |
| HTTP 400, 422, or the service rejects request parameters | Invalid request parameters | Check the interface type, model name, and request format supported by the service. |
| HTTP 401 | API key is invalid | Check that the key for the selected service is correct and valid. |
| Machine translation credential or signature validation failed | Translation credentials or signature are invalid | Check that the app ID and key belong to the same application. For Bing, also check the cloud region and Region. |
| HTTP 403 or the service denies access | Not authorized to use this interface / Not authorized to use this translation service | Confirm that the account has enabled the corresponding interface and that the key has access. |
| HTTP 404 | Endpoint or model does not exist | In AI mode, check the URL and model name. For machine translation or the free version, the upstream interface may have changed. |
| Request timed out, or HTTP 408 or 504 | Request timed out | Check the network. You can increase the request timeout in the game or switch services. |
| HTTP 429 or service rate limiting | Too many requests; try again later | Wait before sending another request and reduce consecutive requests in a short period. |
| Brief backoff after consecutive failures | Please try again later | Wait a moment. If this keeps happening, first troubleshoot the preceding request error. |
| Other HTTP 5xx responses | Service temporarily unavailable | Wait for the upstream service to recover, or switch services in “Interface type.” |
| Network connection failure | Network connection failed | Check whether the selected service is reachable, as well as your network and proxy settings. The free version requires access to Bing web translation. |
| Invalid response structure or content is too long | Returned content is invalid / Returned content is too long | AI services must return JSON in the required format. Check interface and model compatibility, or try another service. |
| Translation account quota is insufficient | Translation quota is insufficient | Check the current provider's balance and quota, or switch services. |
| The service does not support the requested language | This language is not supported | Change the target language in settings or choose a service that supports it. |
| The request could not be queued | Translation request could not be submitted | Wait for messages currently being processed to finish, then try again. |
| Other failures | Translation service error / Translation failed; try again later | Try again later. If failures continue, check local logs, the game version, and whether the mod was deployed correctly. |

## Local logs

Logs are stored in `%LOCALAPPDATA%\HD2ChatTranslate`: `probe` contains startup verification reports, `observe` contains observation reports, and `mailbox` contains translation status reports. At startup, the plugin removes older files of each report type based on last-modified time, keeps at most 10 per type, and reserves a slot for a new report for the current session.

## Documentation

- [Current implementation and technical boundaries](technical.md)
- [Manual build guide](build.md)

## License

Original code in this project is licensed under GNU GPL v3.0 only (SPDX: GPL-3.0-only); see [LICENSE](../LICENSE) for the full text. Third-party cJSON remains under the MIT License. Bingus Shared Loader v18 in the bundled package follows its upstream license and source notices; this project does not relicense that upstream content.
