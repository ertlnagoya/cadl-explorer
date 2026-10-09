#!/usr/bin/env python3
"""
Batch CLI for cadl-explorer — run experiments from the command line.

Usage:
  # Run a single pipeline
  python cli.py run --template "A-SoS + motivation-sensitive" --profile linear --rho 0.5

  # Run a rho sweep
  python cli.py sweep --template "A-SoS + motivation-sensitive" --profile linear

  # Run from experiment YAML
  python cli.py batch --config experiments/a_sos_rho_sweep.yaml

  # Compare two pipeline results
  python cli.py compare --a "A-SoS" --b "A-SoS + motivation-sensitive" --rho 0.5

  # Replay a previous run
  python cli.py replay --run-dir runs/2026-03-28_143000_a_sos_rho_sweep

  # List all runs
  python cli.py list-runs
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))


def cmd_run(args):
    from backend.services.pipeline import run_pipeline
    from backend.services.run_manager import create_run

    pr = run_pipeline(
        template=args.template,
        profile=args.profile,
        rho=args.rho,
        num_seeds=args.seeds,
    )

    print(f"Pipeline: {pr.name}")
    ev = pr.evaluation.to_dict()
    print(f"  Throughput: {ev['throughput']:.1f} +/- {ev['throughput_std']:.1f}")
    print(f"  Autonomy:   {ev['autonomy']:.2f} +/- {ev['autonomy_std']:.2f}")
    print(f"  Fairness:   {ev['fairness']:.2f} +/- {ev['fairness_std']:.2f}")

    if args.save:
        run = create_run(pr.name.replace(" ", "_"))
        run.save_pipeline_result(pr)
        print(f"  Saved to: {run.path}")


def cmd_sweep(args):
    from backend.services.pipeline import run_pipeline
    from backend.services.run_manager import create_run

    rho_values = [float(x) for x in args.rho_values.split(",")]
    print(f"Sweep: {args.template} / {args.profile} / rho={rho_values}")

    for rho in rho_values:
        pr = run_pipeline(
            template=args.template, profile=args.profile,
            rho=rho, num_seeds=args.seeds,
        )
        ev = pr.evaluation.to_dict()
        print(f"  rho={rho:.2f}: tp={ev['throughput']:.1f} au={ev['autonomy']:.2f} fa={ev['fairness']:.2f}")

        if args.save:
            run = create_run(f"sweep_{pr.name}")
            run.save_pipeline_result(pr)


def cmd_batch(args):
    from backend.services.cadl_service import load_experiment_config
    from backend.services.pipeline import run_pipeline
    from backend.services.run_manager import create_run

    config = load_experiment_config(args.config)
    print(f"Batch experiment: {config.name}")

    if config.experiment:
        sw = config.experiment
        for rho in sw.rho:
            for profile in sw.motivation_profiles:
                pr = run_pipeline(
                    template="A-SoS + motivation-sensitive" if config.sos_type == "directed" else "C-SoS",
                    profile=profile, rho=rho,
                    num_seeds=min(len(sw.seeds), args.max_seeds),
                )
                ev = pr.evaluation.to_dict()
                print(f"  {profile}/rho={rho:.2f}: tp={ev['throughput']:.1f} au={ev['autonomy']:.2f} fa={ev['fairness']:.2f}")

                if args.save:
                    run = create_run(f"batch_{pr.name}")
                    run.save_pipeline_result(pr)
    else:
        pr = run_pipeline(
            template="A-SoS" if config.sos_type == "directed" else "C-SoS",
            profile=config.agent_motivation.profile,
            rho=config.governance_motivation.rho,
            num_seeds=args.max_seeds,
        )
        ev = pr.evaluation.to_dict()
        print(f"  tp={ev['throughput']:.1f} au={ev['autonomy']:.2f} fa={ev['fairness']:.2f}")


def cmd_compare(args):
    from backend.services.pipeline import run_pipeline, compare_pipelines

    pr_a = run_pipeline(template=args.a, profile=args.profile, rho=args.rho_a, num_seeds=args.seeds)
    pr_b = run_pipeline(template=args.b, profile=args.profile, rho=args.rho_b, num_seeds=args.seeds)

    diffs = compare_pipelines(pr_a, pr_b)

    print(f"\n{'='*60}")
    print(f"Comparing: {pr_a.name} vs {pr_b.name}")
    print(f"{'='*60}")

    for stage in ("cadl", "ir", "config", "result"):
        sdiff = getattr(diffs, stage)
        if sdiff is None:
            continue
        print(f"\n--- {stage.upper()} ---")
        print(f"  {sdiff.summary}")
        for lbl in sdiff.labels:
            print(f"  [{lbl.category}] {lbl.summary}")


def cmd_replay(args):
    from backend.services.run_manager import replay_run, RunDir, create_run
    from pathlib import Path

    print(f"Replaying: {args.run_dir}")
    pr = replay_run(args.run_dir)
    ev = pr.evaluation.to_dict()
    print(f"  Throughput: {ev['throughput']:.1f}")
    print(f"  Autonomy:   {ev['autonomy']:.2f}")
    print(f"  Fairness:   {ev['fairness']:.2f}")

    if args.save:
        run = create_run(f"replay_{pr.name}")
        run.save_pipeline_result(pr)
        print(f"  Saved to: {run.path}")


def cmd_list_runs(args):
    from pathlib import Path
    from backend.services.run_manager import RUNS_DIR, RunDir

    base = Path(RUNS_DIR)
    if not base.exists():
        print("No runs directory found.")
        return

    dirs = sorted([d for d in base.iterdir() if d.is_dir() and d.name != ".gitkeep"])
    if not dirs:
        print("No runs found.")
        return

    for d in dirs:
        rd = RunDir(d)
        manifest = rd.load_manifest()
        if manifest:
            print(f"  {d.name}  template={manifest.get('template','')}  "
                  f"profile={manifest.get('profile','')}  rho={manifest.get('rho','')}  "
                  f"n={manifest.get('num_results','')}")
        else:
            print(f"  {d.name}  (no manifest)")


def main():
    parser = argparse.ArgumentParser(description="CADL Explorer CLI — batch experiment runner")
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Run a single pipeline")
    p_run.add_argument("--template", default="A-SoS + motivation-sensitive")
    p_run.add_argument("--profile", default="linear")
    p_run.add_argument("--rho", type=float, default=0.5)
    p_run.add_argument("--seeds", type=int, default=10)
    p_run.add_argument("--save", action="store_true", help="Save to runs/")

    # sweep
    p_sweep = sub.add_parser("sweep", help="Run a rho sweep")
    p_sweep.add_argument("--template", default="A-SoS + motivation-sensitive")
    p_sweep.add_argument("--profile", default="linear")
    p_sweep.add_argument("--rho-values", default="0.0,0.25,0.5,0.75,1.0")
    p_sweep.add_argument("--seeds", type=int, default=10)
    p_sweep.add_argument("--save", action="store_true")

    # batch
    p_batch = sub.add_parser("batch", help="Run from experiment YAML")
    p_batch.add_argument("--config", required=True)
    p_batch.add_argument("--max-seeds", type=int, default=10)
    p_batch.add_argument("--save", action="store_true")

    # compare
    p_cmp = sub.add_parser("compare", help="Compare two pipelines")
    p_cmp.add_argument("--a", default="A-SoS")
    p_cmp.add_argument("--b", default="A-SoS + motivation-sensitive")
    p_cmp.add_argument("--profile", default="linear")
    p_cmp.add_argument("--rho-a", type=float, default=0.0)
    p_cmp.add_argument("--rho-b", type=float, default=0.5)
    p_cmp.add_argument("--seeds", type=int, default=10)

    # replay
    p_replay = sub.add_parser("replay", help="Replay a previous run")
    p_replay.add_argument("--run-dir", required=True)
    p_replay.add_argument("--save", action="store_true")

    # list-runs
    sub.add_parser("list-runs", help="List all saved runs")

    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    elif args.command == "sweep":
        cmd_sweep(args)
    elif args.command == "batch":
        cmd_batch(args)
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "replay":
        cmd_replay(args)
    elif args.command == "list-runs":
        cmd_list_runs(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
