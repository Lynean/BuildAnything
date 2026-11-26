---
name: service-connector
description: Connect the app to any outside service, an online API or an MCP server, so agents can use it automatically and tool tabs can show its data. Research it, describe it as a connection, guide the person through setup, test it.
---

# Service-connector

A **connection** lets the app use an outside service: an online API (Altium 365 through Nexar, GitHub, DigiKey, Octopart, a weather or data API…) or an MCP server (Fusion's MCP add-in, a vendor's MCP server…). After the person approves it and fills in its keys:
- **department agents** use it with `use_service(connection, operation, params)`: reading runs at once; anything that changes something waits for the person each time;
- **tool tabs** read from it with `useService(connection, operation, params)` from `@ba/tab`.

The app's server makes every request and adds the credentials itself. No agent, script or tab ever sees a key, and a request can only go to the approved address, for an operation the person approved.

## Steps

1. **Research from official sources.** Find the service's own API or MCP documentation with `web_search` and `open_web_page`: the base URL, how to sign in, what each endpoint or tool does. Prefer the maker's documentation. For an MCP server that's running, `mcp_server_tools(target)` lists its tools. Note what you couldn't confirm and say so in the summary; never guess an address, scope or field name.
2. **Choose the sign-in** (`auth.type`):
   - `none`: public data.
   - `bearer`: a personal token sent as `Authorization: Bearer …`, the most common.
   - `header`: a key in a named header such as `X-API-Key` (set `header_name`).
   - `query`: a key in the URL, such as `?api_key=` (set `query_name`). Use it only if the service offers nothing else.
   - `oauth2` with `grant: "client_credentials"`: an app ID and secret that get a token without the person signing in.
   - `oauth2` with `grant: "authorization_code"`: the person signs in in their browser (needed for personal data, such as Altium 365 workspaces). The app's sign-in return address is shown on the screen; the setup steps must tell the person to add it to their app's allowed redirect URLs.
   - `env` (MCP command servers only): key fields are passed to the server as environment variables named after each field id in capitals.
3. **List the key fields** the person fills in: `id` (lowercase_words), `label`, and `where` (exactly where on the service's site to find it). Mark non-secret ones like a client ID or workspace URL with `"secret": false`.
4. **Write the setup steps** for a beginner, one action per step: create the account or app, where to click, what to copy into which field, the redirect URL to add for browser sign-in, then press Test.
5. **Describe each operation**, one per thing an agent or tab needs:
   - `name` (lowercase_words) and a plain `description`;
   - for an API: `method`, `path` (relative to `base_url`, with `{param}` placeholders), `query`, and `body` (a JSON template: a string that is exactly `"{param}"` becomes the value itself, typed);
   - for MCP: `mcp_tool`, the server's tool name. To fix argument values the agent must not change, such as `"readOnly": true` or a required `queryType`, give `body` as the tool's arguments with `{param}` placeholders for the parts the agent fills in. Then those values are enforced, and **Test** can run the action without arguments;
   - `params`: each with `name`, `type`, `description` and `required`;
   - `writes`: **true if it creates, changes, sends, deletes, orders or publishes anything.** When in doubt, true. PUT, PATCH, DELETE and GraphQL mutations are always writes.
   - Start with a few read operations that answer real questions. Add writes only when the person asks for them.
6. **Pick `check`:** a cheap read operation that proves the key works, such as "who am I" or "list workspaces".
7. **Choose the departments** whose agents may use it.
8. **Propose it** with `propose_connection(connection_json, why)`. Before proposing, read each MCP tool's input schema (`mcp_server_tools`) and make sure the check action supplies every required argument through `body`. Then guide the person: approve it under Connections, fill in each field, sign in if needed, press **Test**. If the test fails, read the error, fix the connection or the steps, and propose again.
9. **Prove the main action returns something** once it's approved: call it once with `call_service` (a read) and look at the reply. An action that "succeeds" with an empty reply (such as a script runner that doesn't return the script's output) can't send data back directly; say so plainly, and note in its description that results must be saved to a file in the project folder (the service runs on this computer) and read from there.
10. **Show it:** once it works, offer a tool tab that shows its data (Tab-creator Skill, `useService`). Run `call_service` first to see the real shape of the data.

## Rules

- **Never ask for a key, password or token in the chat,** and never repeat one. Keys go only into the key fields on the screen.
- **Read-only by default.** A connection that only reads is safer and easier for the person to approve.
- **Least access:** request only the scopes and permissions the operations need. Explain each in the summary.
- **Be honest about what isn't confirmed** (a redirect rule, a field name) and put "check this" in the setup steps instead of presenting a guess as fact.
- **Errors in plain words:** if a call answers 401 or 403, say the key is wrong or lacks permission and which setting to check. Don't just repeat the code.
- **Costs:** if the service charges per call, say so in the summary and keep operations few and specific.
- **No workarounds:** never route around the person's network rules, the service's terms or the app's approvals.

## Examples

`read_example("connection-github")` (bearer token, REST), `read_example("connection-altium-365")` (browser sign-in, GraphQL), `read_example("connection-fusion-mcp")` (an MCP server on this computer).
