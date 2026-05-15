@echo off
REM bench/run.bat — Windows benchmark runner for Anvil P-02
REM Usage:
REM   bench\run.bat                  (default: 5-seed fast mode)
REM   bench\run.bat --quick          (quick 2-seed check)
REM   bench\run.bat --mode deep      (deep mode)

echo ============================================================
echo   ANVIL P-02 ^| Persistent Context Engine ^| Benchmark Runner
echo ============================================================
echo.

set ADAPTER=adapters.engine:Engine

REM Run self_check (displays results)
python self_check.py --adapter %ADAPTER% %*

echo.
echo ------------------------------------------------------------
echo   Generating JSON report...
echo ------------------------------------------------------------

REM Generate JSON report
python -c "import json,sys,importlib;sys.path.insert(0,'.');from harness import run,compute_score;mod=importlib.import_module('adapters.engine');factory=mod.Engine;summary=run(factory,mode='fast');score=compute_score(summary,'fast');report={'adapter':'adapters.engine:Engine','mode':'fast','metrics':{'recall_at_5':score.get('recall@5',0),'precision_at_5_mean':score.get('precision@5_mean',0),'remediation_acc':score.get('remediation_acc',0),'latency_p95_ms':score.get('latency_p95_ms',0)},'weighted_automated':score.get('weighted',0),'max_possible':0.80,'pass':score.get('recall@5',0)>=0.8 and score.get('remediation_acc',0)>=0.8};f=open('report.json','w');json.dump(report,f,indent=2);f.close();print(f'  Report: report.json');print(f'  Score:  {report[\"weighted_automated\"]:.3f} / 0.80');print(f'  PASS:   {report[\"pass\"]}')"

echo.
echo Done.
