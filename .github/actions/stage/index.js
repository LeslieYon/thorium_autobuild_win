const core = require('@actions/core');
const { execSync } = require('child_process');

/**
 * Stage-based build runner for Thorium CI builds.
 * 
 * Each stage runs a portion of the build process. If the build takes too long
 * (approaching GitHub's 6-hour limit), subsequent stages resume from where
 * the previous stage left off using cached artifacts.
 */

const WORKSPACE_ROOT = 'C:\\thorium-autobuild-win';
const MAX_RUNTIME_MS = 4 * 60 * 60 * 1000; // build.py stops Ninja after 3.5 hours

async function run() {
    try {
        const finished = core.getInput('finished');
        const fromArtifact = core.getInput('from_artifact');
        const x86 = core.getInput('x86') === 'true';
        const arm = core.getInput('arm') === 'true';
        const simd = core.getInput('simd') || 'avx2';

        // If previous stage finished, propagate completion
        if (finished === 'true') {
            core.setOutput('finished', 'true');
            return;
        }

        // If resuming from artifact, restore the build state
        if (fromArtifact === 'true') {
            console.log('Restoring build state from previous stage...');
        }

        // Build arguments
        const buildArgs = ['build.py', '--ci', '--simd', simd];
        if (x86) buildArgs.push('--x86');
        if (arm) buildArgs.push('--arm');

        try {
            console.log(`Starting build for ${simd} with args: ${buildArgs.join(' ')}`);
            execSync(`python ${buildArgs.join(' ')}`, {
                cwd: WORKSPACE_ROOT,
                stdio: 'inherit',
                timeout: MAX_RUNTIME_MS
            });

            core.setOutput('finished', 'true');

        } catch (error) {
            if (error.killed || error.signal === 'SIGTERM' || error.code === 'ETIMEDOUT' || error.status === 130) {
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
