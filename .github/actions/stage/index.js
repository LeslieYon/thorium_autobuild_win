const core = require('@actions/core');
const io = require('@actions/io');
const exec = require('@actions/exec');
const {DefaultArtifactClient} = require('@actions/artifact');
const glob = require('@actions/glob');

/**
 * Stage-based build runner for Thorium CI builds.
 *
 * Each stage runs a portion of the build process. If the build takes too long
 * (approaching GitHub's 6-hour limit), subsequent stages resume from where
 * the previous stage left off using cached artifacts.
 */

const WORKSPACE_ROOT = 'C:\\tbw';

async function run() {
    process.on('SIGINT', function() {
    });

    const finished = core.getBooleanInput('finished', {required: true});
    const fromArtifact = core.getBooleanInput('from_artifact', {required: true});
    const x86 = core.getBooleanInput('x86', {required: false});
    const arm = core.getBooleanInput('arm', {required: false});
    const simd = core.getInput('simd') || 'avx2';

    console.log(`finished: ${finished}, artifact: ${fromArtifact}, simd: ${simd}`);

    // If previous stage finished, propagate completion
    if (finished) {
        core.setOutput('finished', true);
        return;
    }

    const artifact = new DefaultArtifactClient();
    // Intermediate artifact (for multi-stage resume) and final artifact names.
    let artifactName, finalArtifactName;
    if (x86) {
        artifactName = 'build-artifact-x86';
        finalArtifactName = 'thorium-x86';
    } else if (arm) {
        artifactName = 'build-artifact-arm';
        finalArtifactName = 'thorium-arm';
    } else {
        artifactName = `build-artifact-${simd}`;
        finalArtifactName = `thorium-${simd}`;
    }

    // If resuming from artifact, restore the build state
    if (fromArtifact) {
        console.log('Restoring build state from previous stage...');
        const artifactInfo = await artifact.getArtifact(artifactName);
        await artifact.downloadArtifact(artifactInfo.artifact.id, {
            path: `${WORKSPACE_ROOT}\\build`
        });
        await exec.exec('7z', ['x', `${WORKSPACE_ROOT}\\build\\artifacts.zip`,
            `-o${WORKSPACE_ROOT}\\build`, '-y']);
        await io.rmRF(`${WORKSPACE_ROOT}\\build\\artifacts.zip`);
    }

    // Build arguments: limit to 2 threads to avoid server overload
    const args = ['build.py', '--ci', '--simd', simd, '-j', '2'];
    if (fromArtifact) args.push('--build-only');
    if (x86) args.push('--x86');
    if (arm) args.push('--arm');

    const retCode = await exec.exec('python', args, {
        cwd: WORKSPACE_ROOT,
        ignoreReturnCode: true
    });

    if (retCode === 0) {
        // Build succeeded: upload final package
        core.setOutput('finished', true);
        console.log('Build completed. Uploading package...');

        const globber = await glob.create(
            `${WORKSPACE_ROOT}\\build\\thorium_*`,
            {matchDirectories: false});
        let packageList = await globber.glob();

        for (let i = 0; i < 5; ++i) {
            try {
                await artifact.deleteArtifact(finalArtifactName);
            } catch (e) {
                // ignored — artifact may not exist yet
            }
            try {
                await artifact.uploadArtifact(
                    finalArtifactName, packageList,
                    `${WORKSPACE_ROOT}\\build`,
                    {retentionDays: 4, compressionLevel: 0});
                console.log('Package uploaded successfully.');
                break;
            } catch (e) {
                console.error(`Upload artifact failed: ${e}`);
                // Wait 10 seconds between retries
                await new Promise(r => setTimeout(r, 10000));
            }
        }
    } else if (retCode === 2) {
        // Build timed out: save build state for next stage, with retries
        console.log(`Build stage for ${simd} timed out (exit code ${retCode}). Saving state...`);

        await new Promise(r => setTimeout(r, 5000));
        await exec.exec('7z', ['a', '-tzip',
            `${WORKSPACE_ROOT}\\artifacts.zip`,
            `${WORKSPACE_ROOT}\\build\\src`,
            '-mx=3', '-mtc=on'],
            {ignoreReturnCode: true});

        for (let i = 0; i < 5; ++i) {
            try {
                await artifact.deleteArtifact(artifactName);
            } catch (e) {
                // ignored
            }
            try {
                await artifact.uploadArtifact(
                    artifactName,
                    [`${WORKSPACE_ROOT}\\artifacts.zip`],
                    WORKSPACE_ROOT,
                    {retentionDays: 1, compressionLevel: 0});
                break;
            } catch (e) {
                console.error(`Upload artifact failed: ${e}`);
                await new Promise(r => setTimeout(r, 10000));
            }
        }

        core.setOutput('finished', false);
    } else {
        // Build failed: stop immediately, do not save state
        core.setFailed(`Build failed with exit code ${retCode}. Not saving state.`);
    }
}

run().catch(err => core.setFailed(err.message));
