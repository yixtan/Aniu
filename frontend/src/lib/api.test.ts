import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getRunDetail, login, updateModelChannel, updateSettings } from "./api";
import { getAuthSession, setAuthSession } from "./auth-session";
import { isApiConflictError } from "./openapi-client";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  setAuthSession({
    authenticated: false,
    identityInitialized: false,
    username: null,
    csrfToken: null,
  });
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function mockedRequest(fetchMock: ReturnType<typeof vi.fn>) {
  const [request] = fetchMock.mock.calls[0] as [Request];
  return request;
}

describe("OpenAPI API client behavior", () => {
  it("creates a typed 409 conflict error and sends the exact expected revision", async () => {
    const conflict = {
      error: {
        code: "ConfigurationConflictError",
        message: "model channel was modified by another request",
        request_id: "req-conflict",
        details: {
          resource: "model_profile",
          expected_revision: 4,
          actual_revision: 5,
        },
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(conflict), {
        status: 409,
        statusText: "Conflict",
        headers: { "Content-Type": "application/json" },
      }),
    );
    globalThis.fetch = fetchMock;

    const request = updateModelChannel(3, {
      name: "Primary",
      protocol: "openai_chat_completions",
      model_name: "gpt-4.1",
      base_url: "https://api.example.test/v1",
      api_key: null,
      enabled: true,
      sort_order: 0,
      expected_revision: 4,
      selected_models: [],
    });

    await expect(request).rejects.toMatchObject({
      name: "ApiConflictError",
      message: "model channel was modified by another request",
      status: 409,
      resource: "model_profile",
      expectedRevision: 4,
      actualRevision: 5,
      requestId: "req-conflict",
      payload: conflict,
    });
    await request.catch((error: unknown) => {
      expect(isApiConflictError(error)).toBe(true);
    });
    const sent = mockedRequest(fetchMock);
    expect(await sent.json()).toMatchObject({ expected_revision: 4 });
    expect(sent.credentials).toBe("include");
  });

  it("adds the current CSRF token to writes and omits undefined JSON fields", async () => {
    setAuthSession({ csrfToken: "csrf-test-token" });
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        revision: 2,
        prompt_profile: {},
        stage_settings: [],
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      }),
    );
    globalThis.fetch = fetchMock;

    const omittedFields: object = { description: undefined };
    await updateSettings({ expected_revision: 1, ...omittedFields });

    const sent = mockedRequest(fetchMock);
    expect(sent.headers.get("X-CSRF-Token")).toBe("csrf-test-token");
    expect(await sent.json()).toEqual({ expected_revision: 1 });
  });

  it("updates the auth session after login", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        authenticated: true,
        identity_initialized: true,
        username: "aniu",
        csrf_token: "next-token",
      }),
    );
    globalThis.fetch = fetchMock;

    await expect(login("secret-token")).resolves.toMatchObject({
      authenticated: true,
      username: "aniu",
      csrfToken: "next-token",
    });
    expect(await mockedRequest(fetchMock).json()).toEqual({ token: "secret-token" });
  });

  it("accepts a run whose public-data calls went to 同花顺", async () => {
    // The trace guard is hand-written, so the compiler cannot say it missed a
    // provider; missing one made every run that used it unreadable.
    globalThis.fetch = vi.fn().mockResolvedValue(
      Response.json({
        id: 7,
        trace: {
          schema_version: 1,
          event_seq: 3,
          current_stage_id: null,
          stages: [
            {
              stage_id: "Run",
              key: "run",
              started_at: null,
              ended_at: null,
              steps: [
                {
                  step_id: "tool-1",
                  type: "tool",
                  title: "市场情绪",
                  status: "completed",
                  summary: null,
                  content: null,
                  started_at: null,
                  ended_at: null,
                  tool_call: {
                    call_id: "call-1",
                    intent_line: "市场情绪",
                    source: "public",
                    tool_name: "market_sentiment",
                    display_name: "市场情绪",
                    query_parameters: "action=reasons",
                    stock_api_calls: [
                      {
                        call_id: "",
                        provider: "ths",
                        interface_name: "公开数据",
                        interface_identifier: "sentiment.limit_reasons",
                        operation_id: "sentiment.limit_reasons",
                        response_characters: 1200,
                        status: "success",
                        duration_ms: 90,
                        error_message: null,
                      },
                    ],
                  },
                },
              ],
            },
          ],
        },
      }),
    );

    await expect(getRunDetail(7)).resolves.toMatchObject({ id: 7 });
  });

  it("clears the session on a 401 response", async () => {
    window.history.replaceState({}, "", "/login");
    setAuthSession({
      authenticated: true,
      username: "aniu",
      csrfToken: "stale-token",
    });
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue(Response.json({ detail: "session expired" }, { status: 401 }));

    await expect(login("secret-token")).rejects.toMatchObject({
      name: "ApiError",
      status: 401,
      message: "session expired",
    });
    expect(getAuthSession()).toMatchObject({
      authenticated: false,
      identityInitialized: true,
      username: null,
      csrfToken: null,
    });
  });
});
