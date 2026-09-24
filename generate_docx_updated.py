from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

document = Document()

# Title
title = document.add_heading('ServiceFlow Vision: Zero-Friction Deployment Plan', 0)
title.alignment = WD_ALIGN_PARAGRAPH.CENTER

# Intro
p_intro = document.add_paragraph()
p_intro.add_run('This deployment strategy is designed to minimize upfront costs by leveraging the hardware the restaurant already owns. By hooking into existing IP cameras and utilizing their existing back-office or POS PCs, the barrier to entry drops to near-zero.').italic = True

# Infographic
document.add_heading('System Architecture', level=1)
try:
    img = document.add_picture(r'C:\Users\afsal\.gemini\antigravity\brain\11eeb1e5-9f5e-4165-ad78-a728ba1c7b29\architecture_infographic_1789282526604.jpg', width=Inches(6.0))
    last_paragraph = document.paragraphs[-1] 
    last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
except Exception as e:
    document.add_paragraph('[Error loading infographic image]')

document.add_paragraph('The diagram above illustrates how the AI runs locally on the existing PC, processing video frames in real-time and instantly deleting them. It only transmits tiny text-based metrics (JSON) to the cloud dashboard, ensuring total privacy and zero bandwidth issues.')

# Section 1
document.add_heading('1. Integrating with Existing Cameras (Cost: ₹0)', level=2)
p1 = document.add_paragraph()
p1.add_run('There is no need to install new cameras or wire the restaurant.\n')
p1.add_run('• Compatibility Check: ').bold = True
p1.add_run('Ensure the restaurant’s existing DVR/NVR (e.g., Hikvision, CP Plus, Dahua) supports RTSP streaming.\n')
p1.add_run('• The Connection: ').bold = True
p1.add_run('The software simply connects to the NVR’s local IP address to pull the 3 live camera feeds over the local Wi-Fi or LAN. No physical tampering with the cameras is required.')

# Section 2
document.add_heading('2. Leveraging the Existing PC (Cost: ₹0 to ₹30,000)', level=2)
p2 = document.add_paragraph()
p2.add_run('Instead of selling them an expensive server, the AI engine is installed on their existing manager or POS computer.\n')
p2.add_run('• Scenario A (Strong PC): ').bold = True
p2.add_run('If their PC has a modern CPU (Intel i5/i7) and at least 16GB of RAM, the OpenVINO AI models will run natively in the background without any hardware upgrades. Hardware Cost: ₹0.\n')
p2.add_run('• Scenario B (Weak PC Upgrade): ').bold = True
p2.add_run('If their billing PC is too weak (e.g., an old Intel i3), running AI might lag their POS software. Instead of replacing the PC, simply install a consumer NVIDIA graphics card (like the RTX 3060) inside it. The GPU will silently handle all the AI heavy lifting. Hardware Cost: ~₹25,000.')

# Section 3
document.add_heading('3. The Cloud Dashboard (Cost: ₹800 – ₹2,800/month)', level=2)
p3 = document.add_paragraph()
p3.add_run('Because no video is ever saved or uploaded to the cloud, monthly server costs are shockingly low.\n')
p3.add_run('• The Data: ').bold = True
p3.add_run('The local PC only sends text alerts (e.g., "Table 2 occupied at 12:05 PM") to the cloud database.\n')
p3.add_run('• Remote Access: ').bold = True
p3.add_run('The restaurant owner can log into the web dashboard from their phone anywhere in the world to view real-time table turnover metrics and staff performance.')

# Section 4
document.add_heading('Final Deployment Economics (in INR)', level=2)
p4 = document.add_paragraph()
p4.add_run('• Upfront Hardware: ').bold = True
p4.add_run('₹0 to ₹30,000 (Worst case: buying a GPU for their PC)\n')
p4.add_run('• Installation / Setup: ').bold = True
p4.add_run('Software installation and 1-hour calibration of the floor plan.\n')
p4.add_run('• Monthly Cloud Hosting: ').bold = True
p4.add_run('₹800 to ₹2,800 (To host the manager dashboard and database).')

document.save('Deployment_Plan_Updated.docx')
print('Updated Docx generated.')
