"""Remote transport: talking to a Hub.

Split from `aethel/core` because this layer touches the network, and from
`aethel/commands` because none of it needs a terminal. Two pieces:

  objects.py   which objects a push must consider -- pure, no network
  http.py      how they travel -- HTTP, no repository knowledge

That split is what makes the interesting half testable without a server: the
push plan is computed from the object store alone, and a wrong plan is a
failing assertion rather than a mystery upload.
"""

__all__ = ["PushPlan", "build_push_plan"]


def __getattr__(name: str):
    # Lazy so `import aethel.remote` does not pull in httpx via http.py.
    if name in __all__:
        from aethel.remote.objects import PushPlan, build_push_plan

        return {"PushPlan": PushPlan, "build_push_plan": build_push_plan}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
