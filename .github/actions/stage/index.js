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
    const x86 = core.getBooleanInput('x86', {required: false});
    const arm = core.getBooleanInput('arm', {required: false});
    const simd = core.getInput('simd') || 'avx2';

    console.log(`finished: ${finished}, simd: ${simd}`);

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

    // Dynamically check if a build artifact exists (from a timed-out previous stage).
    // If so, restore the partial build state and resume with --build-only.
    // Otherwise, restore the prepared source from cache and start fresh.
    let restoredFromArtifact = false;
    try {
        const artifactInfo = await artifact.getArtifact(artifactName);
        if (artifactInfo && artifactInfo.artifact) {
            console.log('Found build artifact. Restoring build state from previous stage...');
            await artifact.downloadArtifact(artifactInfo.artifact.id, {
                path: `${WORKSPACE_ROOT}\\build`
            });
            await new Promise(r => setTimeout(r, 10000));
            await exec.exec('7z', ['x', `${WORKSPACE_ROOT}\\build\\artifacts.zip`,
                `-o${WORKSPACE_ROOT}\\build`, '-y']);
            await io.rmRF(`${WORKSPACE_ROOT}\\build\\artifacts.zip`);
            restoredFromArtifact = true;
        }
    } catch (e) {
        console.log('No build artifact found.');
    }

    if (!restoredFromArtifact) {
        console.log('Restoring prepared source from cache...');
        const cache = require('@actions/cache');
        const cacheKey = core.getInput('cache-key');
        try {
            const hitKey = await cache.restoreCache(
                [`${WORKSPACE_ROOT}\\build\\src`],
                cacheKey,
                []
            );
            if (hitKey) {
                console.log(`Cache restored successfully (key: ${hitKey})`);
            } else {
                console.log('No cache hit found. Source tree may be empty.');
            }
        } catch (cacheError) {
            console.log(`Cache restore failed: ${cacheError.message}. Continuing without cache.`);
        }
    }

    // Build arguments: limit to 2 threads to avoid server overload
    const args = ['build.py', '--ci', '--simd', simd, '-j', '2'];
    if (restoredFromArtifact) args.push('--build-only');
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
        // Build failed: if extra overlay URL is set, save state for debugging
        if (process.env['THORIUM_EXTRA_OVERLAY_URL']) {
            console.log(`Build failed with exit code ${retCode}, but THORIUM_EXTRA_OVERLAY_URL is set. Saving state...`);

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
        }

        // Build failed: stop immediately, do not save state
        core.setFailed(`Build failed with exit code ${retCode}.`);
    }
}

run().catch(err => core.setFailed(err.message));
