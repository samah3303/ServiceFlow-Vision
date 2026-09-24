from docx import Document

document = Document()
document.add_heading('ServiceFlow Vision: 3-Camera Deployment Plan', 0)

document.add_paragraph('For a single room covered by exactly 3 cameras, the deployment plan becomes incredibly streamlined and highly cost-effective. Because you only have 3 camera streams, the hardware and networking requirements drop significantly.')

document.add_heading('1. The Hardware: A Lightweight "Edge" Mini-PC', level=2)
p1 = document.add_paragraph()
p1.add_run('You do not need a massive, expensive server for just 3 cameras.\n')
p1.add_run('• The Device: ').bold = True
p1.add_run('You can use a compact Edge AI device (like an NVIDIA Jetson Orin Nano) or a standard desktop PC with an entry-level consumer GPU (like an RTX 3050 or 4060).\n')
p1.add_run('• Location: ').bold = True
p1.add_run('This small box sits physically in the restaurant\'s office or network closet.')

document.add_heading('2. Live Video Ingestion (Zero Storage Costs)', level=2)
p2 = document.add_paragraph()
p2.add_run('Instead of dealing with massive video files being saved and moved around, the system will run entirely on live streams.\n')
p2.add_run('• The Network: ').bold = True
p2.add_run('The 3 CCTV cameras and the Edge PC are plugged into the same local internet switch.\n')
p2.add_run('• The Stream: ').bold = True
p2.add_run('The Edge PC connects directly to the cameras\' live IP addresses (RTSP feeds). It pulls frames at 3 frames per second, processes the detections in memory, and immediately deletes the frame. No hard drives fill up, and no massive video files are ever saved or uploaded to the cloud.')

document.add_heading('3. Simplified Configuration', level=2)
p3 = document.add_paragraph()
p3.add_run('Because it is a single room with 3 cameras, the setup is a one-time, simple process:\n')
p3.add_run('• You upload the single room\'s floor plan to the Edge PC.\n')
p3.add_run('• You calibrate the 3 cameras once.\n')
p3.add_run('• Because it\'s one room, the cameras likely have overlapping views. You define these "Overlap Zones" in the system so it knows that if a person is seen by Camera 1 and Camera 2 at the exact same time, it\'s the exact same person.')

document.add_heading('4. Cloud Dashboard (The only thing on the internet)', level=2)
p4 = document.add_paragraph()
p4.add_run('The Edge PC does all the heavy lifting locally, but you still want to see the analytics from your phone or laptop at home.\n')
p4.add_run('• The Data Pipeline: ').bold = True
p4.add_run('When the Edge PC detects a service event (e.g., "Table 4 seated" or "Waiter John visited Table 2"), it sends a tiny text message (JSON payload) to a cheap cloud server.\n')
p4.add_run('• The Cost: ').bold = True
p4.add_run('Because it is only sending text, your cloud hosting and internet bandwidth costs will be practically zero (less than $10–$20 a month).')

document.add_heading('Phase 1 Rollout Plan', level=2)
p5 = document.add_paragraph()
p5.add_run('1. Hardware Setup: ').bold = True
p5.add_run('Install the 3 cameras and connect them to a local PC running the modified ServiceFlow Vision engine.\n')
p5.add_run('2. Calibration Day: ').bold = True
p5.add_run('Have an actor walk around the single room, sitting at different tables and crossing between the 3 camera views to ensure the body-tracking hands them off correctly between cameras.\n')
p5.add_run('3. Live Shift Testing: ').bold = True
p5.add_run('Run the system silently during a real shift with your consented actors. Compare the dashboard\'s "First Service" and "Table Turn" times against your own manual notes to verify accuracy before trusting the metrics completely.')

document.save('Deployment_Plan.docx')
print('Docx generated.')
