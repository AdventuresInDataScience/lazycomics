from PIL import Image
import os
print("=== prepared refs ===")
for f in sorted(os.listdir("test_e2e_output/e2e/e2e/refs_prepared")):
    p = Image.open(f"test_e2e_output/e2e/e2e/refs_prepared/{f}")
    print(f"  {f}: {p.size}")
print("=== source fixtures ===")
for f in sorted(os.listdir("tests/fixtures/integration")):
    if f.endswith('.png'):
        p = Image.open(f"tests/fixtures/integration/{f}")
        print(f"  {f}: {p.size}")
