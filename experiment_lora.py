import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import get_peft_model, LoraConfig, TaskType, PeftModel
import os
import shutil
import time
import gc

# Configuration
MODEL_ID = "TinyLlama/TinyLlama-1.1B-Chat-v1.0"
ADAPTER_A_PATH = "./temp_adapter_A"
ADAPTER_B_PATH = "./temp_adapter_B"

def get_memory_usage():
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        return f"{allocated:.2f}GB (Allocated), {reserved:.2f}GB (Reserved)"
    return "N/A (CPU)"

def compare_logits(logits1, logits2, tolerance=1e-5):
    diff = torch.abs(logits1 - logits2).max().item()
    is_close = diff < tolerance
    return is_close, diff

def main():
    print("="*80)
    print("AETHEL-GIT THESIS VALIDATION: AGGRESSIVE TESTING")
    print("="*80)
    print(f"Initial Memory: {get_memory_usage()}")

    # 1. Load Base Model
    print("\n[Step 1] Loading Base Model...")
    try:
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_compute_dtype=torch.float16,
            bnb_4bit_use_double_quant=True
        )
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID, 
            quantization_config=bnb_config,
            device_map="auto"
        )
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        tokenizer.pad_token = tokenizer.eos_token
        print(f"Model Loaded. Memory: {get_memory_usage()}")
    except Exception as e:
        print(f"FATAL: Error loading model: {e}")
        return

    # Prepare input for consistency check
    test_input = "The meaning of life is"
    inputs = tokenizer(test_input, return_tensors="pt").to(model.device)

    # 2. Baseline Inference (Gold Standard)
    print("\n[Step 2] Capturing Baseline Logits (Base Model)...")
    with torch.no_grad():
        outputs = model(**inputs)
        base_logits = outputs.logits.detach().clone()
    print("Baseline logits captured.")

    # 3. Create & Save Adapter A (Simulate 'Branch A')
    print("\n[Step 3] Simulating Training 'Branch A'...")
    config_a = LoraConfig(r=8, lora_alpha=32, target_modules=["q_proj", "v_proj"], task_type="CAUSAL_LM")
    model = get_peft_model(model, config_a)
    
    # Perturb weights to ensure it's different
    with torch.no_grad():
        for name, param in model.named_parameters():
            if "lora_B" in name:
                param.add_(torch.randn_like(param) * 0.5) # Heavy perturbation
    
    model.save_pretrained(ADAPTER_A_PATH)
    print(f"Adapter A saved to {ADAPTER_A_PATH}")
    
    # Capture Branch A Logits
    with torch.no_grad():
        logits_a = model(**inputs).logits
    _, diff_a = compare_logits(base_logits, logits_a)
    print(f"Branch A Distortion vs Base: {diff_a:.6f} (Should be > 0.0)")
    
    # 4. Unload A and Verify Reversibility
    print("\n[Step 4] Unloading Adapter A (Reverting to Base)...")
    model.unload()
    # Note: 'unload()' in PEFT might not fully detach if not done carefully on some versions, 
    # but normally unmerging or unloading via PeftModel is needed. 
    # With get_peft_model returning a PeftModel, .unload() restores the base model.
    # We must ensure variable 'model' is essentially the base model now.

    with torch.no_grad():
        reverted_logits = model(**inputs).logits
    
    is_perfect, diff_revert = compare_logits(base_logits, reverted_logits)
    print(f"Reversion Delta: {diff_revert:.10f}")
    if is_perfect:
        print("SUCCESS: Model perfectly reverted to base state.")
    else:
        print("FAILURE: Model obtained permanent artifacts from Adapter A.")
        
    # 5. Create Branch B (Different weights)
    print("\n[Step 5] Creating 'Branch B' (Simulating Checkout)...")
    # In a real CLI, we would reload the base and load adapter B.
    # Here we simulate loading Adapter B from scratch onto the base
    # First, let's pretend we are loading it fresh
    model.save_pretrained(ADAPTER_B_PATH) # Save a dummy B first (Wait, this is empty config matches A essentially if we don't change it)
    
    # Let's clean up and load A via PeftModel.from_pretrained to test "Loading" mechanics
    print("Loading Adapter A from disk...")
    model = PeftModel.from_pretrained(model, ADAPTER_A_PATH)
    
    print(f"Memory with Adapter A Again: {get_memory_usage()}")
    t0 = time.time()
    model.unload()
    print(f"Time to Unload: {time.time() - t0:.4f}s")
    
    # 6. Switching: Load A -> Unload -> Load B (Simulated)
    # To truly simulate Branch B, let's manually modify weights again on a fresh adapter logic or just re-perturb
    # For this test, verifying the 'unload' speed and correctness is key.
    
    print("\n[Step 6] Stress Test: Rapid Switch Cycle")
    for i in range(3):
        print(f"Cycle {i+1}: Load A -> Infer -> Unload")
        model = PeftModel.from_pretrained(model, ADAPTER_A_PATH)
        with torch.no_grad():
            _ = model(**inputs)
        model.unload()
    
    with torch.no_grad():
        final_logits = model(**inputs).logits
    is_stable, diff_final = compare_logits(base_logits, final_logits)
    
    print("\n" + "="*80)
    print("RESULTS SUMMARY")
    print("="*80)
    print(f"Base Model Integirty After Stress Test: {'PASSED' if is_stable else 'FAILED'}")
    print(f"Max Logit Divergence: {diff_final:.10f}")
    print(f"Final Memory: {get_memory_usage()}")
    
    # Cleanup
    if os.path.exists(ADAPTER_A_PATH): shutil.rmtree(ADAPTER_A_PATH)
    if os.path.exists(ADAPTER_B_PATH): shutil.rmtree(ADAPTER_B_PATH)

if __name__ == "__main__":
    main()
