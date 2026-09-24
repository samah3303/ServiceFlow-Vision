import imageio

reader = imageio.get_reader('data/cameras/50a4618c7b.mp4')
meta = reader.get_meta_data()

writer = imageio.get_writer(
    'data/cameras/50a4618c7b_safe.mp4',
    fps=meta['fps'],
    codec='libx264',
    pixelformat='yuv420p',
    macro_block_size=None
)

for i, frame in enumerate(reader):
    writer.append_data(frame)
    if i % 50 == 0:
        print(f"Processed {i} frames...")

writer.close()
print("Conversion complete!")
