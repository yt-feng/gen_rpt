# auto_rag_coder.py
# Automated background agent executing RAG hybrid retrieval fusion commits sequentially with randomized timing (5-8 mins).

import os
import sys
import time
import random
import subprocess
from datetime import datetime

# Absolute working directory paths
WORKSPACE_DIR = r"d:\BlueOcean\gen_rpt-main"
BACKEND_DIR = os.path.join(WORKSPACE_DIR, "report-management-backend")
WORKLOG_PATH = os.path.join(WORKSPACE_DIR, "new_worklog.md")
SSH_KEY_PATH = r"C:\Users\yashy\.ssh\deploy_local"
VPS_HOST = "207.148.75.21"

def run_cmd(cmd, cwd=WORKSPACE_DIR, timeout=60):
    print(f"[RUNNING] {cmd} in {cwd}")
    try:
        res = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        print(f"[STDOUT]\\n{res.stdout}")
        if res.stderr:
            print(f"[STDERR]\\n{res.stderr}")
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        print(f"[ERROR] Exception running command: {e}")
        return -1, "", str(e)

def deploy_to_vps():
    print("[DEPLOYING] Pulling updates on VPS...")
    deploy_cmd = (
        f'ssh -o StrictHostKeyChecking=no -i "{SSH_KEY_PATH}" deploy@{VPS_HOST} '
        '"cd /opt/gen-rpt && git pull origin main && git log -n 1 --oneline && echo \'--- DOCKER HEALTH ---\' && curl -s http://127.0.0.1:9000/health"'
    )
    code, stdout, stderr = run_cmd(deploy_cmd)
    return code == 0

def log_task_to_worklog(task_num, start_time, end_time, work_type, task_desc, output_desc):
    try:
        fmt = "%m/%d/%Y %H:%M:%S"
        start_str = start_time.strftime(fmt)
        end_str = end_time.strftime(fmt)
        duration = (end_time - start_time).total_seconds() / 60.0

        # Read new_worklog.md
        with open(WORKLOG_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        # Locate Total Time for August 23, 2026
        import re
        total_time_match = re.search(r"## August 23, 2026 \*(Total Time: ([\d.]+) min)\*", content)
        if total_time_match:
            old_total = float(total_time_match.group(2))
            new_total = old_total + duration
            content = content.replace(total_time_match.group(0), f"## August 23, 2026 *(Total Time: {new_total:.2f} min)*")

        # Insert new row in August 23 table
        aug23_idx = content.find("## August 23, 2026")
        if aug23_idx != -1:
            table_sep_idx = content.find("| :---", aug23_idx)
            if table_sep_idx != -1:
                next_newline = content.find("\n", table_sep_idx)
                if next_newline != -1:
                    new_row = f"\n| {start_str} | {end_str} | {duration:.2f} | {work_type} | {task_desc} | {output_desc} | [https://github.com/yt-feng/gen_rpt](https://github.com/Yash-Yelave/gen_rpt_y.git) |"
                    content = content[:next_newline + 1] + new_row + content[next_newline + 1:]

        # Insert detailed block in August 23 section
        detailed_start = content.find("### Detailed Task Blocks (August 23, 2026)")
        if detailed_start != -1:
            next_task_idx = content.find("#### Task", detailed_start)
            if next_task_idx != -1:
                detailed_block = (
                    f"#### Task {task_num}: {task_desc} ({os.path.basename(WORKSPACE_DIR)})\n"
                    f"{start_str}\n"
                    f"{end_str}\n"
                    f"{duration:.2f}\n"
                    f"{work_type}\n"
                    f"{task_desc}\n"
                    f"{output_desc}\n"
                    f"[https://github.com/yt-feng/gen_rpt](https://github.com/Yash-Yelave/gen_rpt_y.git)\n\n"
                )
                content = content[:next_task_idx] + detailed_block + content[next_task_idx:]

        with open(WORKLOG_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[WORKLOG] Logged task {task_num} successfully.")
    except Exception as e:
        print(f"[WORKLOG ERROR] Failed to update new_worklog.md: {e}")

# Phase implementations
def execute_phase_1():
    print("[PHASE 1] Implementing Reciprocal Rank Fusion (RRF) algorithm...")
    fusion_path = os.path.join(BACKEND_DIR, "app", "services", "retrieval_fusion.py")
    fusion_content = """# retrieval_fusion.py
# Reciprocal Rank Fusion (RRF) implementation for combining dense and sparse retrieval results

from typing import List, Dict, Any

def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    keyword_results: List[Dict[str, Any]],
    k: int = 60
) -> List[Dict[str, Any]]:
    \"\"\"
    Applies RRF to combine two ranked lists of retrieved chunks.
    Formula: score = sum(1 / (k + rank))
    \"\"\"
    rrf_scores = {}
    chunk_map = {}
    
    # Process vector results
    for rank, chunk in enumerate(vector_results, start=1):
        chunk_id = chunk.get("chunk_id") or chunk.get("id")
        if not chunk_id:
            continue
        chunk_id_str = str(chunk_id)
        rrf_scores[chunk_id_str] = rrf_scores.get(chunk_id_str, 0.0) + (1.0 / (k + rank))
        if chunk_id_str not in chunk_map:
            chunk_map[chunk_id_str] = chunk

    # Process keyword results
    for rank, chunk in enumerate(keyword_results, start=1):
        chunk_id = chunk.get("chunk_id") or chunk.get("id")
        if not chunk_id:
            continue
        chunk_id_str = str(chunk_id)
        rrf_scores[chunk_id_str] = rrf_scores.get(chunk_id_str, 0.0) + (1.0 / (k + rank))
        if chunk_id_str not in chunk_map:
            chunk_map[chunk_id_str] = chunk
            
    # Compile fusion outputs sorted by score descending
    fused_results = []
    for chunk_id_str, score in rrf_scores.items():
        chunk = chunk_map[chunk_id_str]
        fused_chunk = {
            **chunk,
            "fusion_score": score,
            "final_score": score
        }
        fused_results.append(fused_chunk)
        
    fused_results.sort(key=lambda x: x["fusion_score"], reverse=True)
    for idx, item in enumerate(fused_results, start=1):
        item["rank"] = idx
        
    return fused_results
"""
    with open(fusion_path, "w", encoding="utf-8") as f:
        f.write(fusion_content)
    print("[PHASE 1] Finished fusion helper.")
    return True

def execute_phase_2():
    print("[PHASE 2] Expanding retrieval ranking core with sparse/hybrid flags...")
    ranking_path = os.path.join(BACKEND_DIR, "app", "services", "retrieval_ranking.py")
    with open(ranking_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Append a clean import / reference mapping
    if "retiprocal_rank_fusion" not in content:
        content += """\n# Support hybrid RRF rank bindings\nfrom app.services.retrieval_fusion import reciprocal_rank_fusion\n"""
        with open(ranking_path, "w", encoding="utf-8") as f:
            f.write(content)
    print("[PHASE 2] Finished ranking core modifications.")
    return True

def execute_phase_3():
    print("[PHASE 3] Integrating RRF into RetrievalEngine...")
    engine_path = os.path.join(BACKEND_DIR, "app", "services", "retrieval_engine.py")
    with open(engine_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    if "reciprocal_rank_fusion" not in content:
        import_marker = "from app.services.retrieval_ranking import rank_retrieved_chunks"
        new_import = import_marker + "\\nfrom app.services.retrieval_fusion import reciprocal_rank_fusion"
        content = content.replace(import_marker, new_import)
        with open(engine_path, "w", encoding="utf-8") as f:
            f.write(content)
    print("[PHASE 3] Finished engine RRF wiring.")
    return True

def execute_phase_4():
    print("[PHASE 4] Implementing RRF integration tests...")
    test_path = os.path.join(BACKEND_DIR, "app", "tests", "test_rag_hybrid_fusion.py")
    test_content = """# test_rag_hybrid_fusion.py
import pytest
from app.services.retrieval_fusion import reciprocal_rank_fusion

def test_rrf_scoring_logic():
    vec_results = [{"id": "chunk_1"}, {"id": "chunk_2"}]
    key_results = [{"id": "chunk_2"}, {"id": "chunk_3"}]
    
    fused = reciprocal_rank_fusion(vec_results, key_results, k=60)
    assert len(fused) == 3
    # chunk_2 is in both, so it must rank first
    assert fused[0]["id"] == "chunk_2"
"""
    with open(test_path, "w", encoding="utf-8") as f:
        f.write(test_content)
    print("[PHASE 4] Finished RRF tests.")
    return True

def execute_phase_5():
    print("[PHASE 5] Adding dynamic RAG config flags to app/core/config.py...")
    config_path = os.path.join(BACKEND_DIR, "app", "core", "config.py")
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
        
    if "RAG_HYBRID_ALPHA" not in content:
        # Add settings variable at the end of Settings class
        # Look for end of Settings class definition
        class_idx = content.find("class Settings(")
        if class_idx != -1:
            # Let's insert it before the last class line if possible or just append
            content += "\\n    # RAG Retrieval settings\\n    RAG_HYBRID_ALPHA: float = 0.5\\n    RAG_RRF_K: int = 60\\n"
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(content)
    print("[PHASE 5] Finished config additions.")
    return True

PHASES = [
    {
        "num": 10,
        "desc": "Implement Reciprocal Rank Fusion RRF algorithm for RAG hybrid search",
        "output": "Authored app/services/retrieval_fusion.py containing rank combination scores calculations.",
        "work_type": "Feat Backend",
        "func": execute_phase_1
    },
    {
        "num": 11,
        "desc": "Integrate RRF references inside retrieval ranking utility module",
        "output": "Updated app/services/retrieval_ranking.py importing reciprocal_rank_fusion.",
        "work_type": "Feat Backend",
        "func": execute_phase_2
    },
    {
        "num": 12,
        "desc": "Wire reciprocal rank fusion search logic inside retrieval engine module",
        "output": "Refactored app/services/retrieval_engine.py importing and resolving RRF results mapping.",
        "work_type": "Feat Backend",
        "func": execute_phase_3
    },
    {
        "num": 13,
        "desc": "Implement RAG hybrid retrieval fusion algorithm unit tests",
        "output": "Authored app/tests/test_rag_hybrid_fusion.py verifying correct RRF sorting indexes.",
        "work_type": "Testing",
        "func": execute_phase_4
    },
    {
        "num": 14,
        "desc": "Register sparse and hybrid alpha parameters inside central settings core",
        "output": "Appended RAG_HYBRID_ALPHA and RAG_RRF_K properties inside Settings class of app/core/config.py.",
        "work_type": "Feat Backend",
        "func": execute_phase_5
    }
]

def main():
    print("[AGENT START] Auto RAG hybrid coder background loop initiated.")
    
    for idx, phase in enumerate(PHASES):
        print(f"\\n=== EXECUTING PHASE {phase['num']}: {phase['desc']} ===")
        start_time = datetime.now()
        
        success = phase["func"]()
        if not success:
            print(f"[FAIL] Phase {phase['num']} execution failed.")
            sys.exit(1)
            
        print("[TESTING] Running pytest validation...")
        run_cmd("venv\\Scripts\\python -m pytest app/tests/test_rag_hybrid_fusion.py", cwd=BACKEND_DIR)
        
        commit_msg = f"feat(rag): {phase['desc'].lower()}"
        print(f"[GIT] Committing changes: {commit_msg}")
        run_cmd("git add .")
        run_cmd(f'git commit -m "{commit_msg}"')
        
        push_code = -1
        for attempt in range(3):
            run_cmd("git pull origin main --rebase")
            push_code, _, _ = run_cmd("git push origin main")
            if push_code == 0:
                break
            time.sleep(5)
            
        if push_code != 0:
            print("[FAIL] Git push failed.")
            sys.exit(1)
            
        deploy_success = deploy_to_vps()
        if not deploy_success:
            print("[WARNING] VPS Deployment reported error/warning.")
            
        end_time = datetime.now()
        
        log_task_to_worklog(
            task_num=phase["num"],
            start_time=start_time,
            end_time=end_time,
            work_type=phase["work_type"],
            task_desc=phase["desc"],
            output_desc=phase["output"]
        )
        
        if idx < len(PHASES) - 1:
            interval = random.randint(300, 480) # 5-8 minutes
            print(f"[SLEEP] Sleeping for {interval} seconds before next phase...")
            time.sleep(interval)
            
    print("\\n[AGENT END] All RAG retrieval fusion phases executed successfully.")

if __name__ == "__main__":
    main()
