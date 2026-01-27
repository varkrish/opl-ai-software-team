@echo off

rem Packaging script for Windows
rem Ensure pyinstaller is installed: pip install pyinstaller

echo 🔨 Building standalone executable for Windows...

rem Clean previous builds
echo 🧹 Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

rem Run PyInstaller using the spec file
echo 📦 Running PyInstaller...
pyinstaller --clean pyinstaller.spec

rem Check if build succeeded
if exist "dist\ai_software_dev_crew.exe" (
    echo ✅ Packaging complete!
    echo 📁 Executable is located at: .\dist\ai_software_dev_crew.exe
    echo 💡 You can run it with: .\dist\ai_software_dev_crew.exe "Build a calculator"
) else (
    echo ❌ Build failed - executable not found
    exit /b 1
)
