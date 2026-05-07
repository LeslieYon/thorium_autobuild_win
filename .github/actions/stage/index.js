const core = require('@actions/core');
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

/**
 * Stage-based build runner for Thorium CI builds.
 * 
 * Each stage runs a portion of the build process. If the build takes too long
 * (approaching GitHub's 6-hour limit), subsequent stages resume from where
 * the previous stage left off using cached artifacts.
 */

const BUILD_DIR = 'C:\\thorium-autobuild-win\\build\\src';
const MAX_RUNTIME_MS = 3.5 * 60 * 60 * 1000; // 3.5 hours

async function run() {
    try {
        const finished = core.getInput('finished');
        const fromArtifact = core.getInput('from_artifact');
        const x86 = core.getInput('x86') === 'true';
        const arm = core.getInput('arm') === 'true';
        const simd = core.getInput('simd') || 'avx2';

        const outputDirName = 'thorium_' + simd;
        const OUTPUT_DIR = path.join(BUILD_DIR, 'out', outputDirName);
        const STAGE_MARKER = path.join(BUILD_DIR, 'out', outputDirName, '.stage_complete');

        // If previous stage finished, propagate completion
        if (finished === 'true') {
            core.setOutput('finished', 'true');
            return;
        }

        // If resuming from artifact, restore the build state
        if (fromArtifact === 'true') {
            console.log('Restoring build state from previous stage...');
        }

        // Change to workspace directory
        process.chdir('C:\\thorium-autobuild-win');

        // Build arguments
        const buildArgs = ['build.py', '--ci', '--simd', simd];
        if (x86) buildArgs.push('--x86');
        if (arm) buildArgs.push('--arm');

        try {
            console.log(`Starting build for ${simd} with args: ${buildArgs.join(' ')}`);
            execSync(`python3 ${buildArgs.join(' ')}`, {
                cwd: 'C:\\thorium-autobuild-win',
                stdio: 'inherit',
                timeout: MAX_RUNTIME_MS
            });

            // If we get here, build completed successfully
            fs.writeFileSync(STAGE_MARKER, 'complete');
            core.setOutput('finished', 'true');

            // Upload build artifacts
            console.log('Uploading build artifacts for ' + simd + '...');
            execSync(`python3 package.py --simd ${simd}`, {
                cwd: 'C:\\thorium-autobuild-win',
                stdio: 'inherit'
            });

        } catch (error) {
            if (error.killed || error.signal === 'SIGTERM') {
                console.log(`Build stage for ${simd} timed out. Saving state for next stage...`);
                core.setOutput('finished', 'false');
            } else {
                throw error;
            }
        }

    } catch (error) {
        core.setFailed(error.message);
    }
}

run();
