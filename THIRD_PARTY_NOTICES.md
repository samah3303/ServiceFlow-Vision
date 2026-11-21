# Third-party models and algorithm references

ServiceFlow Vision uses the following local model artifacts. This file records provenance for reproducible prototype builds; it is not legal advice.

## OpenCV Zoo model artifacts

Source repository: https://github.com/opencv/opencv_zoo

The relevant model directories state that the files in those directories are licensed under Apache License 2.0. Consult each upstream directory and its current licence before redistribution.

- `face_detection_yunet_2023mar.onnx` — YuNet face detector  
  Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet  
  SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
- `face_recognition_sface_2021dec.onnx` — SFace face embeddings  
  Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface  
  SHA-256: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`
- `object_detection_yolox_2022nov.onnx` — YOLOX-S object detector  
  Source: https://github.com/opencv/opencv_zoo/tree/main/models/object_detection_yolox  
  SHA-256: `c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063`

Apache License 2.0: https://www.apache.org/licenses/LICENSE-2.0

## OpenVINO Open Model Zoo artifact

- `person-reidentification-retail-0288.xml` and `.bin` — whole-body person re-identification, FP16 OpenVINO IR  
  Model documentation: https://docs.openvino.ai/2023.3/omz_models_model_person_reidentification_retail_0288.html  
  Official model storage: https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/models_bin/1/person-reidentification-retail-0288/FP16/  
  XML SHA-256: `0d5021c772e603dce69d9ef2e326ecf0a43234cc8b4c5bb5e8aa566f090bdb51`  
  BIN SHA-256: `08e24c2f1dd976ff8e1f667b93db2950ac8db8da09e163e1ca6463b82d1dbc42`

Open Model Zoo repository and licensing: https://github.com/openvinotoolkit/open_model_zoo

## ByteTrack algorithm reference

The local tracker in `app/tracking.py` is a compact independent implementation of the two-stage high/low-confidence association idea described by ByteTrack. It does not vendor the reference source code.

- Paper: Yifu Zhang et al., “ByteTrack: Multi-Object Tracking by Associating Every Detection,” ECCV 2022 — https://arxiv.org/abs/2110.06864
- Reference repository (MIT License): https://github.com/ifzhang/ByteTrack

## RapidOCR timestamp recognition

The optional local OCR dependency `rapidocr` reads timestamps already burned into camera frames. Its Python package includes OCR model files installed with the dependency; those files are not copied into this repository's `models/` folder. RapidOCR is published under Apache License 2.0; review its model and package notices before redistribution.

- Project and licence: https://github.com/RapidAI/RapidOCR

## Deliberately not bundled

This prototype does not bundle Ultralytics, BoxMOT or InsightFace software or weights. Their licences and model-specific terms require a separate product/legal decision if considered later.
