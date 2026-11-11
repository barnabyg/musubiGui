"""Read-only AdamW8bit capability check through the selected training Python."""

import json


def main() -> None:
    supported = False
    try:
        import bitsandbytes as bnb  # type: ignore[import-not-found]
        import torch  # type: ignore[import-not-found]
        from bitsandbytes.cextension import lib  # type: ignore[import-not-found]

        # Constructing the optimizer on CPU does not run a training step or allocate CUDA tensors.
        bnb.optim.AdamW8bit([torch.nn.Parameter(torch.ones(1))], lr=0.0001)
        supported = bool(lib.compiled_with_cuda and torch.cuda.is_available())
    except Exception:
        # Do not expose upstream errors containing environment paths or credentials.
        pass
    print("MUSUBI_OPTIMIZERS=" + json.dumps({"adamw8bit": supported}))


if __name__ == "__main__":
    main()
