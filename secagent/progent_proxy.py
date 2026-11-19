import argparse
import copy
import os
import sys
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.proxy import ProxyClient
from mcp import ErrorData
from mcp.types import TextContent
from .mcp_policy_binding import MCPPolicyBindings
from .tool import current_policy_session, check_tool_call, generate_security_policy, generate_update_security_policy, update_available_tools, update_always_allowed_tools, Tool
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn
import threading
import httpx
import json
from fastmcp.server.dependencies import get_http_headers
from fastmcp.exceptions import McpError
import re

mcp_server = FastMCP.as_proxy(
    ProxyClient("http://127.0.0.1:9001/mcp/"),
    name="Progent MCP Proxy"
)

rest_api = FastAPI(title="MCP API")

# Target server for proxying requests
API_PROXY_TARGET = "http://127.0.0.1"

ENABLE_SECAGENT = os.getenv("ENABLE_SECAGENT", "False").lower() == "true"

POLICY_SESSION_HEADER = "x-progent-policy-token"
_policy_bindings = MCPPolicyBindings()
bind_policy_session = _policy_bindings.bind


@rest_api.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def proxy_request(path: str, request: Request):
    """Forward requests for a task registered by the trusted host."""
    if ENABLE_SECAGENT and _policy_bindings.resolve(request.headers.get(POLICY_SESSION_HEADER)) is None:
        raise HTTPException(status_code=403, detail="Missing or expired Progent task binding.")
    body = await request.body()

    print("="*50 + "\n")

    # Forward the request
    target_url = f"{API_PROXY_TARGET}/{path}"

    try:
        async with httpx.AsyncClient() as client:
            # Prepare headers
            headers = dict(request.headers)
            new_headers = {key: headers[key] for key in ("authorization", "content-type") if key in headers}

            response = await client.request(
                method=request.method,
                url=target_url,
                headers=new_headers,
                content=body,
                params=request.query_params,
                timeout=600.0
            )

            # Print response details
            # print(f"Response Status: {response.status_code}")
            # print(f"Response Headers: {dict(response.headers)}")

            # Parse response content once
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    response_content = response.json()
                    # print(f"Response Body: {json.dumps(response_content, indent=2)}")
                except:
                    response_content = response.text
                    # print(f"Response Body: {response_content[:500]}")
            else:
                response_content = response.text
                # print(f"Response Body: {response_content[:500]}")
            # print("\n")

            # Return response with appropriate headers
            return JSONResponse(
                content=response_content if isinstance(response_content, dict) else {"data": response_content},
                status_code=response.status_code
            )

    except httpx.RequestError as e:
        # print(f"Error proxying request: {e}")
        raise HTTPException(status_code=502, detail=f"Error connecting to target server: {str(e)}")


def convert_mcp_tool_to_progent(tool) -> Tool:
    """Convert MCP tool to Progent tool format"""
    args = copy.deepcopy(tool.parameters["properties"])
    return Tool(
        name=tool.name,
        description=tool.description,
        args=args
    )


agent_expert_tool_list = {
    "general": ["get_user_info",
                "update_password",
                "update_user_info"],
    "workspace": ["read_file",
                  "send_email",
                  "delete_email",
                  "get_unread_emails",
                  "get_sent_emails",
                  "get_received_emails",
                  "get_draft_emails",
                  "search_emails",
                  "search_contacts_by_name",
                  "search_contacts_by_email",
                  "get_current_day",
                  "search_calendar_events",
                  "get_day_calendar_events",
                  "create_calendar_event",
                  "cancel_calendar_event",
                  "reschedule_calendar_event",
                  "add_calendar_event_participants",
                  "append_to_file",
                  "search_files_by_filename",
                  "create_file",
                  "delete_file",
                  "get_file_by_id",
                  "list_files",
                  "share_file",
                  "search_files"],
    "slack": ["get_channels",
              "add_user_to_channel",
              "read_channel_messages",
              "read_inbox",
              "send_direct_message",
              "send_channel_message",
              "get_users_in_channel",
              "invite_user_to_slack",
              "remove_user_from_slack"],
    "banking": ["get_iban",
                "send_money",
                "schedule_transaction",
                "update_scheduled_transaction",
                "get_balance",
                "get_most_recent_transactions",
                "get_scheduled_transactions"],
    "travel": ["get_user_information",
               "get_all_hotels_in_city",
               "get_hotels_prices",
               "get_rating_reviews_for_hotels",
               "get_hotels_address",
               "get_all_restaurants_in_city",
               "get_cuisine_type_for_restaurants",
               "get_restaurants_address",
               "get_rating_reviews_for_restaurants",
               "get_dietary_restrictions_for_all_restaurants",
               "get_contact_information_for_restaurants",
               "get_price_for_restaurants",
               "check_restaurant_opening_hours",
               "get_all_car_rental_companies_in_city",
               "get_car_types_available",
               "get_rating_reviews_for_car_rental",
               "get_car_fuel_options",
               "get_car_rental_address",
               "get_car_price_per_day",
               "reserve_hotel",
               "reserve_car_rental",
               "reserve_restaurant",
               "get_flight_information"],
    "web": ["get_webpage",
            "post_webpage"]
}

suite = os.getenv('SECAGENT_SUITE', None)

def init_policies(*, session=None):
    if session is None:
        session = current_policy_session()
    if suite == "banking":
        update_always_allowed_tools(["get_most_recent_transactions"], allow_all_no_arg_tools=True, session=session)
    elif suite == "slack":
        update_always_allowed_tools(["get_channels", "read_channel_messages", "read_inbox", "get_users_in_channel"], session=session)
    elif suite == "travel":
        update_always_allowed_tools([
            "get_user_information",
            "get_all_hotels_in_city",
            "get_hotels_prices",
            "get_rating_reviews_for_hotels",
            "get_hotels_address",
            "get_all_restaurants_in_city",
            "get_cuisine_type_for_restaurants",
            "get_restaurants_address",
            "get_rating_reviews_for_restaurants",
            "get_dietary_restrictions_for_all_restaurants",
            "get_contact_information_for_restaurants",
            "get_price_for_restaurants",
            "check_restaurant_opening_hours",
            "get_all_car_rental_companies_in_city",
            "get_car_types_available",
            "get_rating_reviews_for_car_rental",
            "get_car_fuel_options",
            "get_car_rental_address",
            "get_car_price_per_day",
            "search_calendar_events",
            "get_day_calendar_events",
            "get_flight_information"
        ], session=session)
    elif suite == "workspace":
        update_always_allowed_tools([
            "get_unread_emails",
            "get_sent_emails",
            "get_received_emails",
            "get_draft_emails",
            "search_emails",
            "search_contacts_by_name",
            "search_contacts_by_email",
            "get_current_day",
            "search_calendar_events",
            "get_day_calendar_events",
            "search_files_by_filename",
            "get_file_by_id",
            "list_files",
            "search_files"
        ], session=session)
    elif suite is not None:
        raise ValueError(f"Unknown suite: {suite}")



def _require_policy_session(headers):
    session = _policy_bindings.resolve(headers.get(POLICY_SESSION_HEADER))
    if session is None:
        raise McpError(ErrorData(code=-32000, message="Missing or expired Progent task binding."))
    return session


class ListingFilterMiddleware(Middleware):
    async def on_list_tools(self, context: MiddlewareContext, call_next):
        headers = get_http_headers()
        binding = _require_policy_session(headers) if ENABLE_SECAGENT else None
        result = await call_next(context)

        if binding is not None and not binding.ready:
            session = binding.session
            update_available_tools([convert_mcp_tool_to_progent(tool) for tool in result], session=session)
            if session.security_policy is None:
                init_policies(session=session)
            if session.user_query is not None:
                generate_security_policy(session.user_query, session=session)
            binding.ready = session.security_policy is not None

        agent_expert = headers.get('agent_expert', None)
        if agent_expert:
            print(f"Agent Expert: {agent_expert}", file=sys.stderr)
            expert_tools = []
            if agent_expert in ["general", "workspace", "slack", "banking", "travel", "web"]:
                for tool in result:
                    if tool.name in agent_expert_tool_list[agent_expert]:
                        expert_tools.append(tool)
                return expert_tools

        return result


class ToolCallFilterMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name, tool_kwargs = context.message.name, context.message.arguments or {}
        binding = _require_policy_session(get_http_headers()) if ENABLE_SECAGENT else None
        session = None
        if binding is not None:
            if not binding.ready:
                raise McpError(ErrorData(code=-32000, message="Task tool policy is not initialized."))
            session = binding.session
            if session.security_policy is None:
                raise McpError(ErrorData(code=-32000, message="Task tool policy is not initialized."))
            check_tool_call(tool_name, tool_kwargs, session=session)

        result = await call_next(context)
        flag = os.getenv("SECAGENT_UPDATE", "True").lower() == "true"
        if session is not None and flag and result.content and isinstance(result.content[0], TextContent):
            last_tool_call = {"name": tool_name, "args": tool_kwargs}
            generate_update_security_policy(
                [last_tool_call], result.content[0].text, manual_check=False, session=session
            )
        return result


mcp_server.add_middleware(ListingFilterMiddleware())
mcp_server.add_middleware(ToolCallFilterMiddleware())


def run_rest_api(port: int):
    """Run the REST API server"""
    print(f"Starting REST API server on port {port}")
    uvicorn.run(rest_api, host="0.0.0.0", port=port, log_level="error")


def run_mcp_proxy(port: int):
    """Run the MCP proxy server"""
    print(f"Starting MCP Proxy Server on port {port}")
    mcp_server.run(
        transport="http", host="0.0.0.0", port=port, log_level="ERROR", stateless_http=True
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Progent Proxy"
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=9100,
        help="Port to run the API server on (default: %(default)s)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=9101,
        help="Port to run the MCP server on (default: %(default)s)",
    )
    parser.add_argument(
        "--api-proxy-target",
        type=str,
        default="https://api.openai.com",
        help="Target URL for API proxy (default: %(default)s)",
    )

    args = parser.parse_args()

    API_PROXY_TARGET = args.api_proxy_target

    rest_thread = threading.Thread(
        target=run_rest_api,
        args=(args.api_port,),
        daemon=True
    )
    rest_thread.start()

    run_mcp_proxy(args.mcp_port)
