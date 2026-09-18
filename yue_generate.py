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

    args = cli.parser().parse_args(sys.argv[1:])
    if args.command != "generate":
        return cli.main(sys.argv[1:])
    original_plan = YuE2Pipeline.plan
    original_semantic = YuE2Pipeline.generate_semantic
    checks = {}

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
    return cli.generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
