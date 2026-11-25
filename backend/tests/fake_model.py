"""An offline stand-in for a model, with the Agents SDK's interface."""
import json

from agents import ModelResponse, Usage
from agents.models.interface import Model
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText


class TestModel(Model):
    __test__ = False

    def __init__(self, custom_output_text=None, custom_output_args=None, call_tools=None):
        self.text, self.args, self.call_tools = custom_output_text, custom_output_args, call_tools or []
        self.turn = 0

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, *, previous_response_id=None, conversation_id=None, prompt=None):
        self.turn += 1
        if self.turn == 1 and self.call_tools:
            output = []
            for tool in tools:
                if tool.name in self.call_tools:
                    arguments = {k: "a" for k in tool.params_json_schema.get("required", [])}
                    output.append(ResponseFunctionToolCall(id=f"fc_{tool.name}", call_id=f"call_{tool.name}", name=tool.name, arguments=json.dumps(arguments), type="function_call", status="completed"))
            self.turn = 1
            self._after_tools = True
            return ModelResponse(output=output, usage=Usage(requests=1, input_tokens=10, output_tokens=5, total_tokens=15), response_id="r1")
        text = json.dumps(self.args) if self.args is not None else (self.text if self.text is not None else "ok")
        message = ResponseOutputMessage(id="m1", role="assistant", status="completed", type="message", content=[ResponseOutputText(text=text, type="output_text", annotations=[])])
        return ModelResponse(output=[message], usage=Usage(requests=1, input_tokens=10, output_tokens=max(1, len(text.split())), total_tokens=20), response_id="r2")

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError
