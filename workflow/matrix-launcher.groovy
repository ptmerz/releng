utils = load 'releng/workflow/utils.groovy'
matrixbuild = load 'releng/workflow/matrixbuild.groovy'
utils.initBuildRevisions('gromacs')
utils.checkoutDefaultProject()

def loadMatrixConfigs(filename)
{
    matrix = matrixbuild.processMatrixConfigs(filename)
}

def doBuild(matrixJobName)
{
    def result = matrixbuild.doMatrixBuild(matrixJobName, matrix)
    utils.combineResultToCurrentBuild(result.build.result)
    utils.processRelengStatus(result.status)
    matrixbuild.addSummaryForMatrix(result)
    setGerritReview customUrl: result.build.absoluteUrl
}

return this
