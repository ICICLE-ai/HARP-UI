# OSC TAPIS system issues found in HARP

**Systems reviewed:** `ascend-tapis`, `pitzer-tapis`, and `cardinal-tapis`  
**Live partition check:** November 9, 2026  
**TAPIS job-history review:** November 6, 2026

## Summary

HARP can reach all three OSC systems, but several Slurm partitions are currently unavailable and some TAPIS system definitions need correction. The live probe found every TAPIS-configured queue name on its corresponding cluster; the main queue problem is that some configured partitions are inactive or drained, not that their names are absent from Slurm.

One important correction: the bad job-directory issue was observed on **Pitzer**, not Cardinal. Cardinal's reviewed failures were caused by missing allocation information.

## Ascend

Currently unavailable Slurm partitions:

- `batch` - inactive
- `cpuonly` - drained
- `debug` - inactive
- `gpu` - drained
- `preemptible` - inactive

TAPIS/configuration issues:

- `batch` and `systems` have `maxNodeCount=0`, which is not usable for normal job requests.
- Live partitions `condo-antitsa` and `longgpu` are not represented by TAPIS logical queues.
- Recent failures included missing allocation/account values, unavailable partitions, insufficient resources, and one application failure with exit code 127.

Recommended action: correct the zero-node queue limits, add or intentionally exclude the unmapped live partitions, and require a valid OSC allocation before submission. HARP should continue hiding the currently inactive or drained partitions by default.

## Pitzer

Currently unavailable Slurm partitions:

- `batch` - inactive
- `debug` - inactive

TAPIS/configuration issues:

- Nine reviewed jobs using `gpu`, `debug-cpu`, or `hugemem` failed before reaching Slurm. TAPIS could not create the job directory under `/fs/ess/scratch/PZS0710/jobs/...` for effective user `mrblacklicorice` and returned HTTP 403.
- Those failure records also referenced `ascend.osc.edu` while processing Pitzer jobs. The current Pitzer system host is `pitzer.osc.edu`, so the execution-host and working-directory configuration should be checked together.
- `batch`, `condo-peterson-olivucci`, `condo-peterson-olivucci-backfill`, and `systems` have `maxNodeCount=0` in TAPIS.
- Live partitions `condo-anzenbacher-olivucci` and `condo-anzenbacher-olivucci-backfill` are not represented by TAPIS logical queues.
- Several TAPIS limits differ from OSC's documented limits, including maximum nodes on `cpu`, `cpu-exp`, `gpu`, `gpu-exp`, and `gpu-quad`; maximum cores on `debug-cpu`; and maximum time/node limits on `hugemem` and `largemem`.

Recommended action: point Pitzer jobs at a Pitzer directory writable by the effective user, verify the system host used during job setup, correct the zero-node queue limits, and synchronize the remaining TAPIS limits with OSC.

## Cardinal

Currently unavailable Slurm partitions:

- `batch` - inactive

TAPIS/configuration issues:

- The three reviewed Cardinal failures were allocation/account errors. No Cardinal job-directory failure was found in the reviewed jobs.
- The live `longcpu` partition is not represented by a TAPIS logical queue. Several condo partitions are also live but unmapped: `condo-ccapp`, `condo-ccapp-backfill`, and `condo-haiphan`.
- TAPIS memory limits for `cpu` and `hugemem` differ from OSC's documented values.

Recommended action: require a valid Cardinal allocation before submission, add or intentionally exclude `longcpu` and the condo queues, and reconcile the memory limits with OSC.
