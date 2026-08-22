"""LangGraph StateGraph construction for agentic chat.

Builds a Petri Net parallel-transition graph:

    START -> state -> perception -> evaluation -> decision
                                             ├─→ projection -> END
                                             └─→ inventory -> inventory_exec -> END

No loops. No conditional routing. Each node is a pure function that
transforms the shared ChatState.

Petri net model based token aggregation:
  - state_node collects tokens (schedule, crop, environment, growth_stage)
  - waits until all tokens are present (checkpoint)
  - then triggers the agent to fire

Petri net parallel transitions:
  - decision_node outputs a prescription token (JSON)
  - The token is placed into state, triggering TWO independent transitions:
      1. projection_node — Format prescription → human-readable message
      2. inventory_node  — Check stock levels for prescribed drugs
  - Both transitions converge at END
"""

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END

from .state import ChatState
from .nodes import (
    state_node,
    perception_node,
    evaluation_node,
    decision_node,
    projection_node,
    inventory_node,
    inventory_exec_node,
)


def build_graph() -> StateGraph:
    """Construct and compile the agentic chat graph.

    Returns:
        Compiled LangGraph application (Petri Net parallel-transition graph).
    """
    # Define the graph
    builder = StateGraph(ChatState)

    # Add the nodes (state first for token aggregation)
    builder.add_node("state", state_node)
    builder.add_node("perception", perception_node)
    builder.add_node("evaluation", evaluation_node)
    builder.add_node("decision", decision_node)

    # Projection transition
    builder.add_node("projection", projection_node)

    # Inventory transition (new — parallel independent)
    builder.add_node("inventory", inventory_node)
    builder.add_node("inventory_exec", inventory_exec_node)

    # Set the entry point
    builder.set_entry_point("state")

    # Sequential portion (linear DAG)
    builder.add_edge("state", "perception")
    builder.add_edge("perception", "evaluation")
    builder.add_edge("evaluation", "decision")

    # Branch — prescription token released, two transitions fire in parallel
    builder.add_edge("decision", "projection")
    builder.add_edge("decision", "inventory")

    # Converge — both transitions reach END
    builder.add_edge("projection", END)
    builder.add_edge("inventory", "inventory_exec")
    builder.add_edge("inventory_exec", END)

    # Compile with a memory checkpointer for thread-based history
    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
