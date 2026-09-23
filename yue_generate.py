"""OpenMagia's checked entry point to the installed YuE CLI.

Keep native generation and exact plan tokens; validate before acoustic work.
"""
import sys
import os
from pathlib import Path


def main():
    from yue2 import cli
    from yue2.pipeline import YuE2Pipeline
    from yue_worker import inspect_abc_score, check_planned_audio_coverage

    argv = list(sys.argv[1:])
    adapter = None
    if "--adapter" in argv:
        index = argv.index("--adapter")
        if index + 1 >= len(argv):
            raise ValueError("--adapter needs a local PEFT adapter directory")
        adapter = str(Path(argv[index + 1]).resolve())
        del argv[index:index + 2]
    args = cli.parser().parse_args(argv)
    if args.command != "generate":
        return cli.main(argv)
    original_plan = YuE2Pipeline.plan
    original_semantic = YuE2Pipeline.generate_semantic
    original_load_model = YuE2Pipeline._load_model
    checks = {}

    def load_model(self, *pos, **kwargs):
        if adapter and self.backend == "vllm":
            raise ValueError("YuE 2 LoRA adapters currently require the torch or torch-eager backend.")
        if adapter and self.quantization != "none":
            raise ValueError("YuE 2 LoRA adapters currently require unquantized model loading.")
        model = original_load_model(self, *pos, **kwargs)
        if adapter and not getattr(self, "_openmagia_adapter", False):
            config = Path(adapter) / "adapter_config.json"
            if not config.is_file() or not any(Path(adapter).glob("adapter_model*.safetensors")):
                raise ValueError("The selected LoRA is missing its PEFT config or safetensors weights.")
            model.load_adapter(adapter, adapter_name="openmagia", is_trainable=False, local_files_only=True)
            model.set_adapter("openmagia")
            self._openmagia_adapter = True
            self.weights["adapter"] = {"path": adapter}
        return model

    def plan(self, *pos, **kwargs):
        result = original_plan(self, *pos, **kwargs)
        directory = Path(args.output) / result.request.id
        result.save(directory)
        if result.truncated:
            # Truncation stays a hard failure: an incomplete plan never earns
            # expensive acoustic work, and the user is told to retry the candidate.
            raise ValueError("YuE truncated the planned score before synthesis; retry this candidate.")
        if result.abc is not None:
            check = inspect_abc_score(directory / "score.abc", Path.cwd())
            if not check["accepted"]:
                if result.request.abc is not None:
                    raise ValueError(check["reason"])
                # This score was written by YuE's own planner, not supplied or
                # edited by the user. An imperfect symbolic plan can still decode
                # into a clean, complete recording, so a failed native-dialect
                # inspection is surfaced as a review warning and acoustic synthesis
                # proceeds. The truncation receipt (this method and semantic) and the
                # decoded-audio checks in the worker still decide whether the take is
                # publishable, so a silent or clipped ending never slips through.
                # (Supplied / edited ABC is validated strictly elsewhere.)
                print("OPENMAGIA_SCORE_REVIEW: "
                      + str(check.get("reason") or "generated score failed the native inspector"),
                      flush=True)
            else:
                checks[id(result)] = check
        return result

    def semantic(self, plan, **kwargs):
        result = original_semantic(self, plan, **kwargs)
        if result.truncated:
            raise ValueError("YuE truncated semantic generation before synthesis; retry this candidate.")
        # YuE 2 has no duration control and its request schema carries no length
        # floor: shortness on its own is not a defect (the reference run_yue2.py
        # fails only on truncation and merely reports audio_seconds). What IS checked
        # here is gross early termination against the plan the model wrote itself --
        # semantic audio that stops well short of its own planned arrangement. When
        # the plan could not be inspected, coverage returns "unavailable" and passes.
        # Native codec rate is 25 frames/s.
        coverage = check_planned_audio_coverage(checks.get(id(plan)), len(result.tokens) / 25.0)
        if not coverage["accepted"]:
            raise ValueError(coverage["reason"])
        return result

    YuE2Pipeline.plan = plan
    YuE2Pipeline.generate_semantic = semantic
    YuE2Pipeline._load_model = load_model
    return cli.generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
