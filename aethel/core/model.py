import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import get_peft_model, LoraConfig, TaskType, PeftModel
import os
import shutil
from typing import Optional
from rich.console import Console

console = Console()

class ModelManager:
    def __init__(self, model_id="TinyLlama/TinyLlama-1.1B-Chat-v1.0"):
        self.model_id = model_id
        self.tokenizer = None
        self.model = None

    def load_base_model(self):
        """Loads the base model with quantization if available."""
        console.print(f"[bold blue]Loading base model: {self.model_id}...[/bold blue]")
        
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(self.model_id)
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
        except Exception as e:
            console.print(f"[bold red]Failed to load tokenizer: {e}[/bold red]")
            return False

        try:
            from transformers import BitsAndBytesConfig
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True
            )
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_id, 
                quantization_config=bnb_config,
                device_map="auto"
            )
            console.print("[green]Loaded with 4-bit quantization.[/green]")
        except Exception as e:
            console.print(f"[yellow]Quantization failed ({e}). Falling back to standard loading.[/yellow]")
            try:
                self.model = AutoModelForCausalLM.from_pretrained(
                    self.model_id,
                    torch_dtype=torch.float16,
                    device_map="auto"
                )
            except Exception as e:
                console.print(f"[bold red]FATAL: Could not load model: {e}[/bold red]")
                return False
        
        return True

    def create_or_load_adapter(self, adapter_path=None):
        """
        If adapter_path is provided, load it.
        If not, initialize a new trainable LoRA adapter.
        """
        if adapter_path and os.path.exists(adapter_path):
             console.print(f"[blue]Loading adapter from {adapter_path}...[/blue]")
             self.model = PeftModel.from_pretrained(self.model, adapter_path)
             self.model.train() # Make sure it's trainable if we are continuing work
        else:
            console.print("[blue]Initializing new LoRA adapter...[/blue]")
            peft_config = LoraConfig(
                task_type=TaskType.CAUSAL_LM, 
                inference_mode=False, 
                r=8, 
                lora_alpha=32, 
                lora_dropout=0.1,
                target_modules=["q_proj", "v_proj"]
            )
            self.model = get_peft_model(self.model, peft_config)
        
        self.model.print_trainable_parameters()

    def train_dummy_step(self):
        """
        Simulates training by randomly perturbing weights (for PoC).
        In real usage, this would run a training loop.
        """
        console.print("[yellow]Simulating training step (perturbing weights)...[/yellow]")
        with torch.no_grad():
            for name, param in self.model.named_parameters():
                if "lora_B" in name:
                    param.add_(torch.randn_like(param) * 0.05)

    def save_adapter(self, output_path: str) -> bool:
        """Save adapter artifacts into a staging directory."""
        if self.model is None:
            console.print("[bold red]Cannot save adapter: model is not loaded.[/bold red]")
            return False

        console.print(f"[blue]Saving adapter to {output_path}...[/blue]")

        try:
            if os.path.exists(output_path):
                shutil.rmtree(output_path)
            os.makedirs(output_path, exist_ok=True)
            self.model.save_pretrained(output_path)
            return True
        except OSError as e:
            console.print(f"[bold red]File system error while saving adapter: {e}[/bold red]")
            return False
        except Exception as e:
            console.print(f"[bold red]Failed to save adapter: {e}[/bold red]")
            return False

    def get_adapter_weights_path(self, adapter_dir: str) -> Optional[str]:
        """Return the adapter weight file path from a saved adapter directory."""
        safetensors_path = os.path.join(adapter_dir, "adapter_model.safetensors")
        bin_path = os.path.join(adapter_dir, "adapter_model.bin")

        if os.path.isfile(safetensors_path):
            return safetensors_path
        if os.path.isfile(bin_path):
            return bin_path

        console.print("[bold red]No adapter weight file found in staging directory.[/bold red]")
        return None
