mkdir gromacs
cd gromacs
git init && git fetch ssh://jenkins@gerrit.gromacs.org:29418/gromacs refs/heads/release-4-5-patches && git checkout -q -f FETCH_HEAD && python -u ../GerritBuild.py
